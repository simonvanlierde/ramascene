# ramascene-jobs

Thin job API around the RaMa-Scene engine: one scenario per job, one job at a time

Submit a scenario in the explorer's format, poll for the result:

| Endpoint | What |
| --- | --- |
| `POST /jobs` | A scenario: `{"label": ..., "model_details": [{product, originReg, consumedBy, consumedReg, techChange}], "reference_check": false}`. Ids are the engine database's global ids; `techChange` is one finite number in percent, -100 to 1000. 202 with the job and a `Location`; 422 with every problem; 503 when the queue is full or the engine is not configured. |
| `GET /jobs/{id}` | Status (`queued`, `running`, `succeeded`, `failed`), duration and peak RSS of the job's process, and the result: baseline and scenario by region for three indicators, the embodied `flows`, plus provenance (engine commit, dataset hashes). `flows` holds absolute values (not a difference) for baseline and scenario as flat row-major float64 lists of `shape` `[3, 49, 15, 49]`, axes `indicator`, `producer`, `section`, `consumer`, in each indicator's unit (as in `results`); `sections` names the 15 product aggregates, countries are in engine index order. The result is a few MB of JSON, so responses are gzipped when the client sends `Accept-Encoding: gzip`. |
| `GET /catalog` | The ids a scenario may use, with names. |
| `GET /health` | Liveness. |

Each job runs in its own subprocess, one at a time, so the engine's ~1.65 GB
peak never overlaps another job's and is returned to the OS afterwards. The API
process never imports the engine. The parent measures each job's duration and
peak RSS itself (`wait4`), returns both in the job record, and reports both as
OpenTelemetry histograms (`ramascene_jobs.job.duration`,
`ramascene_jobs.job.peak_rss`) and a `ramascene_jobs.job` span when
`OTEL_EXPORTER_OTLP_ENDPOINT` is set. The API hashes the four dataset files
once, when it starts, and again only when one changes (size or modification
time); the worker gets the hashes with the job. Jobs live in
memory: a restart forgets them, and only the latest 20 finished jobs are kept
(a result with flows takes about 7 MB), so older ones answer 404. At most 4
jobs wait; a job is killed after 900 s.
Browsers may call the API from loopback origins (`http://127.0.0.1:<port>`,
`http://localhost:<port>`) only.

Configuration (environment): `DATASETS_DIR`, `DATASETS_VERSION` (default `v4`),
`RAMASCENE_DB` (default: the engine checkout's `db.sqlite3`), `HOST` (default
`127.0.0.1`), `PORT` (default `8020`), and `OTEL_EXPORTER_OTLP_ENDPOINT` and
`OTEL_SERVICE_NAME` (default `ramascene-jobs`).

The engine is a path dependency on the RaMa-Scene checkout this folder lives in
(`..`), packaged by its `pyproject.toml`.

```sh
DATASETS_DIR=/path/to/datasets uv run ramascene-jobs    # 127.0.0.1:8020
DATASETS_DIR=/path/to/datasets just up                  # the same in Docker
```

The correctness gate (`tests/test_engine.py`, deselected by default) runs real
jobs through the API: the explorer's steel-into-vehicles -20% scenario must
reproduce its committed `data/results.json` within relative 1e-9, its flows must
match the explorer's `data/embodied.parquet` (baseline absolute, scenario as the
change from baseline; float32 storage, so relative 1e-6 per cell plus a floor of
1e-6 of the indicator's largest baseline cell, and the same for each indicator's
total), and a 0% change must reproduce all 122 Octave reference values the
engine ships. `EXPLORER_EMBODIED` points at the Parquet file.

```sh
DATASETS_DIR=/path/to/datasets uv run pytest -m engine -s
```

On 2 October 2026 both tests passed: the scenario matched `data/results.json`
to 5.3e-15 relative, the 122 Octave values matched within 1e-6 (largest
relative difference 4.2e-8), and on a 32-core machine each job took under 2 s
with a peak RSS of 1.53 GiB.

On 21 October 2026 all three passed with the flows included: the worst relative
flows difference against the Parquet was 6.0e-8 (float32 rounding), and each job
took under 2 s with a peak RSS of 1.52 GiB.

## Development

```sh
uv sync
just             # lists the tasks
just check       # lint, format check, types and tests
just up          # build and run the service in its container (compose project ramascene_jobs)
```

`tests/benchmarks` holds benchmarks of the worker's numpy work on synthetic
inputs of the real shapes. A plain `pytest` runs each once as a test;
`uv run pytest tests/benchmarks --codspeed` times them, and CodSpeed measures
them in CI (`.github/workflows/codspeed.yml`).

## License

GPL-3.0-or-later, the same as the rest of RaMa-Scene; see [LICENSE](../LICENSE).
