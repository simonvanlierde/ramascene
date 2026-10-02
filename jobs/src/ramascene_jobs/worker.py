"""One job, in its own process: `python -m ramascene_jobs.worker IN.json OUT.json`.

The API never imports the engine. Each job gets a fresh interpreter, so the
~1.65 GB the engine peaks at on a technical-coefficient scenario is returned to
the OS when the job ends, and a crash or an out-of-memory kill takes down the
job, not the API.

The engine is driven the way ramascene.tasks.execute_calc drives it, without
Celery, a broker or channels, and the way the explorer's precompute.py does:
route three (Hotspot x Geographic, where the impact occurs) for three
indicators, broken down by the five continental regions. The numbers are
therefore directly comparable with the explorer's data/results.json.
"""

import gc
import hashlib
import json
import math
import os
import platform
import resource
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from typing import Any

import numpy as np

YEAR = 2011
FINAL_CONSUMPTION = "FINALCONSUMPTION"  # ModellingProduct.identifier of "Y: Final Consumption"
# The explorer's breakdown and indicators (precompute.py), as global ids.
REGIONS = [2, 3, 4, 5, 6]  # Europe, Middle-East, America, Asia-Pacific, Africa
INDICATORS = [5, 3, 7]  # GHG emissions: Total; Employment: Total; DEU - Metal Ores - Total
DATASET = {
    "name": "EXIOBASE v3.3.sm",
    "doi": "10.5281/zenodo.3533196",
    "year": YEAR,
}
# The default of pytest.approx, which the engine's regression harness uses
# against the same Octave references.
REFERENCE_REL, REFERENCE_ABS = 1e-6, 1e-12
LEGACY_VOCABULARY = {
    "Consumption": "Contribution",
    "Production": "Hotspot",
    "GeoMap": "Geographic",
    "TreeMap": "Sectoral",
}
ROUTES = {
    ("Contribution", "Geographic"): "route_two",
    ("Hotspot", "Geographic"): "route_three",
    ("Contribution", "Sectoral"): "route_one",
    ("Hotspot", "Sectoral"): "route_four",
}


def setup_django(engine_db: Path, datasets_dir: Path, datasets_version: str, tmp: Path) -> None:
    """Configure Django for the engine's ORM, on a copy of its database.

    A copy because ramascene/__init__.py switches SQLite to WAL mode, which
    writes to the database it touches.
    """
    import django  # noqa: PLC0415
    from django.conf import settings  # noqa: PLC0415

    db = tmp / "db.sqlite3"
    shutil.copy(engine_db, db)
    settings.configure(
        INSTALLED_APPS=["django.contrib.contenttypes", "django.contrib.auth", "ramascene"],
        DATABASES={"default": {"ENGINE": "django.db.backends.sqlite3", "NAME": str(db)}},
        DATASET_DIR=str(datasets_dir),
        DATASET_VERSION=datasets_version,
        USE_TZ=True,
    )
    django.setup()


def run_route(selection: dict[str, Any], y: Any, b: Any, leontief: Any) -> tuple[dict[str, float], Any]:  # noqa: ANN401
    """One Analyze route, argument for argument as tasks.execute_calc builds it."""
    from ramascene import querymanagement as qm  # noqa: PLC0415
    from ramascene.analyze import Analyze  # noqa: PLC0415

    ready = qm.calc_ready_selection(selection)
    analyze = Analyze(
        qm.convert_to_numpy(ready["nodesSec"]),
        qm.convert_to_numpy(ready["nodesReg"]),
        qm.convert_to_numpy(ready["extn"]),
        selection,
        ready["idx_units"],
        "ramascene-jobs",
        0,
        np.arange(0, 49),
        y,
        b,
        leontief,
    )
    result = json.loads(getattr(analyze, ROUTES[(selection["dimType"], selection["vizType"])])())
    return {name: float(v) for name, v in result["rawResultData"].items()}, result["unit"]


def by_region(y: Any, b: Any, leontief: Any) -> dict[str, dict[str, float]]:  # noqa: ANN401
    """{indicator id: {region name: value}}, as in the explorer's results."""
    out = {}
    for indicator in INDICATORS:
        selection = {
            "dimType": "Hotspot",
            "vizType": "Geographic",
            "nodesSec": [1],
            "nodesReg": list(REGIONS),
            "extn": [indicator],
            "year": YEAR,
        }
        values, _ = run_route(selection, y, b, leontief)
        if not all(math.isfinite(v) for v in values.values()):
            msg = f"non-finite engine output for indicator {indicator}: {values}"
            raise ValueError(msg)
        out[str(indicator)] = values
    return out


def apply_model(model_details: list[dict[str, Any]], y: Any) -> tuple[Any, Any]:  # noqa: ANN401
    """(Y, L) after the intervention: consumers.py's preprocessing, then Modelling.

    For a technical-coefficient scenario L is the engine's LeontiefSolve, which
    solves (I - A) x = y rather than holding an inverse.
    """
    from ramascene import querymanagement as qm  # noqa: PLC0415
    from ramascene.modelling import Modelling  # noqa: PLC0415

    ready: dict[str, list[Any]] = {
        k: [] for k in ("product", "originReg", "consumedReg", "consumedBy", "techChange", "identifiers")
    }
    load_a = []
    for step in model_details:
        ready["product"].append(qm.get_leafs_product(step["product"]))
        ready["originReg"].append(qm.get_leafs_country(step["originReg"]))
        ready["consumedReg"].append(qm.get_leafs_country(step["consumedReg"]))
        ready["consumedBy"].append(qm.get_leafs_modelled_product(step["consumedBy"]))
        ready["techChange"].append(step["techChange"])
        identifier = qm.identify_modelling_product(step["consumedBy"][0])
        ready["identifiers"].append(identifier)
        load_a.append(identifier != FINAL_CONSUMPTION)
    return Modelling(ready, y, load_a, YEAR, model_details).apply_model()


def reference_validation(y: Any, b: Any, leontief: Any) -> dict[str, Any]:  # noqa: ANN401
    """The four Octave reference queries the engine ships, on these matrices.

    The same check as the regression harness's
    test_scenario_solve_matches_octave_reference: it reproduces only on
    unchanged matrices, i.e. for a 0% change.
    """
    from ramascene.tests.validation_data import (  # noqa: PLC0415
        FILES_TO_TEST_AGAINST,
        VALIDATION_DIR,
        open_validation_file,
    )

    files = {}
    worst = 0.0
    for filename in FILES_TO_TEST_AGAINST:
        query, expected, expected_unit = open_validation_file(str(Path(VALIDATION_DIR) / filename))
        selection = query["querySelection"]
        selection["year"] = int(selection["year"][0])
        selection["dimType"] = LEGACY_VOCABULARY[selection["dimType"]]
        selection["vizType"] = LEGACY_VOCABULARY[selection["vizType"]]
        got, unit = run_route(selection, y, b, leontief)
        mismatches = [
            k
            for k, v in expected.items()
            if not math.isclose(got.get(k, math.nan), v, rel_tol=REFERENCE_REL, abs_tol=REFERENCE_ABS)
        ]
        rel = [abs(got[k] - v) / abs(v) for k, v in expected.items() if k in got and v]
        worst = max([worst, *rel])
        files[filename] = {
            "values": len(expected),
            "mismatches": len(mismatches),
            "unit_matches": unit == expected_unit,
            "max_relative_difference": max(rel, default=0.0),
        }
    return {
        "passed": all(f["mismatches"] == 0 and f["unit_matches"] for f in files.values()),
        "tolerance": f"relative {REFERENCE_REL:g}, absolute {REFERENCE_ABS:g} (pytest.approx default)",
        "values": sum(f["values"] for f in files.values()),
        "max_relative_difference": worst,
        "files": files,
    }


def engine_provenance() -> dict[str, Any]:
    """Which engine code ran.

    The git checkout the installed `ramascene` package lives in (an editable
    install), or else what the image was built from (RAMASCENE_ENGINE_COMMIT and
    RAMASCENE_ENGINE_BRANCH, set from build arguments).
    """
    import ramascene  # noqa: PLC0415

    root = Path(ramascene.__file__).resolve().parents[1]
    if not (root / ".git").exists() or shutil.which("git") is None:
        return {
            "branch": os.environ.get("RAMASCENE_ENGINE_BRANCH"),
            "commit": os.environ.get("RAMASCENE_ENGINE_COMMIT"),
            "dirty": None,
        }

    def git(*args: str) -> str | None:
        done = subprocess.run(["git", "-C", str(root), *args], capture_output=True, text=True, check=False)  # noqa: S603, S607
        return done.stdout.strip() if done.returncode == 0 else None

    return {
        "branch": git("branch", "--show-current"),
        "commit": git("rev-parse", "HEAD"),
        "dirty": bool(git("status", "--porcelain", "--untracked-files=no")),
    }


def sha256(path: Path) -> str:
    """Of one dataset file, so a result names the exact matrices it came from."""
    with path.open("rb") as f:
        return hashlib.file_digest(f, "sha256").hexdigest()


def run(job: dict[str, Any]) -> dict[str, Any]:
    """Scenario, then baseline, one after the other so their matrices never overlap."""
    import django  # noqa: PLC0415
    from ramascene import querymanagement as qm  # noqa: PLC0415

    scenario = job["scenario"]
    datasets_dir = Path(job["datasets_dir"])
    timings: dict[str, float] = {}
    started = time.perf_counter()

    y, b = qm.get_numpy_objects(YEAR, "Y"), qm.get_numpy_objects(YEAR, "B")
    y_s, leontief = apply_model(scenario["model_details"], y)
    scenario_results = by_region(y_s, b, leontief)
    validation = reference_validation(y_s, b, leontief) if scenario["reference_check"] else None
    del y_s, leontief
    gc.collect()
    timings["scenario_s"] = time.perf_counter() - started

    mark = time.perf_counter()
    leontief = qm.get_numpy_objects(YEAR, "L")
    baseline = by_region(y, b, leontief)
    del leontief
    gc.collect()
    timings["baseline_s"] = time.perf_counter() - mark

    names = qm.get_names_indicator(INDICATORS)
    units = qm.get_indicator_units(INDICATORS)
    suffix = job["datasets_version"]
    mark = time.perf_counter()
    hashes = {n: sha256(datasets_dir / str(YEAR) / f"{n}_{suffix}.npy") for n in "ALYB"}
    timings["hash_s"] = time.perf_counter() - mark
    return {
        "regions": list(next(iter(baseline.values()))),
        "indicators": [{"id": i, "name": n, "unit": units[n]} for i, n in zip(INDICATORS, names, strict=True)],
        "results": {"baseline": baseline, "scenario": scenario_results},
        "reference_validation": validation,
        "provenance": {
            "engine": engine_provenance(),
            "dataset": {**DATASET, "file_suffix": suffix, "sha256": hashes},
            "python": platform.python_version(),
            "numpy": np.__version__,
            "django": django.get_version(),
            "worker_peak_rss_bytes": resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
            * (1 if sys.platform.startswith("darwin") else 1024),  # KiB on Linux, bytes on macOS
            "timings": timings,
        },
    }


def main(argv: list[str] | None = None) -> None:
    """Read the job, run it, write the result; exit 1 with the error in OUT on failure."""
    in_path, out_path = (Path(a) for a in (argv or sys.argv[1:]))
    job = json.loads(in_path.read_text())
    try:
        with tempfile.TemporaryDirectory() as tmp:
            setup_django(Path(job["engine_db"]), Path(job["datasets_dir"]), job["datasets_version"], Path(tmp))
            result = run(job)
    except Exception as e:  # noqa: BLE001 - reported to the API, which marks the job failed
        out_path.write_text(json.dumps({"error": f"{type(e).__name__}: {e}"}))
        sys.exit(1)
    out_path.write_text(json.dumps(result, allow_nan=False))


if __name__ == "__main__":
    main()
