# ramascene-jobs

Thin job API around the RaMa-Scene engine: one scenario per job, one job at a time

Submit a scenario in the explorer's format, poll for the result:

| Endpoint | What |
| --- | --- |
| `POST /jobs` | A scenario: `{"label": ..., "model_details": [{product, originReg, consumedBy, consumedReg, techChange}], "reference_check": false}`. Ids are the engine database's global ids; `techChange` is one finite number in percent, -100 to 1000. 202 with the job and a `Location`; 422 with every problem; 503 when the queue is full or the engine is not configured. |
| `GET /jobs/{id}` | Status (`queued`, `running`, `succeeded`, `failed`), duration and peak RSS of the job's process, and the result: baseline and scenario by region for three indicators, plus provenance (engine commit, dataset hashes). |
| `GET /catalog` | The ids a scenario may use, with names. |
| `GET /health` | Liveness. |

Each job runs in its own subprocess, one at a time, so the engine's ~1.65 GB
peak never overlaps another job's and is returned to the OS afterwards. The API
process never imports the engine. The parent measures each job's duration and
peak RSS itself (`wait4`), and reports both as OpenTelemetry histograms
(`ramascene_jobs.job.duration`, `ramascene_jobs.job.peak_rss`) and a
`ramascene_jobs.job` span when `OTEL_EXPORTER_OTLP_ENDPOINT` is set.

Configuration (environment): `DATASETS_DIR`, `DATASETS_VERSION` (default `v4`),
`RAMASCENE_DB` (default: the engine checkout's `db.sqlite3`), `MAX_QUEUED_JOBS`
(4), `JOB_TIMEOUT_S` (900), `JOB_LOCK_FILE` (flock held around each engine run),
`JOBS_DIR` (write each finished job as JSON), `CORS_ORIGINS` (loopback origins
are always allowed), and the template's `HOST`, `PORT`, `OTEL_*`.

The engine is a path dependency on the RaMa-Scene checkout this folder lives in
(`..`), packaged by its `pyproject.toml`.

```sh
DATASETS_DIR=/path/to/datasets uv run ramascene-jobs    # 127.0.0.1:8020
DATASETS_DIR=/path/to/datasets just up                  # the same in Docker
```

The correctness gate (`tests/test_engine.py`, deselected by default) runs real
jobs through the API: the explorer's steel-into-vehicles -20% scenario must
reproduce its committed `data/results.json` within relative 1e-9, and a 0%
change must reproduce all 122 Octave reference values the engine ships.

```sh
DATASETS_DIR=/path/to/datasets uv run pytest -m engine -s
```

## Development

```sh
uv sync
just             # lists the tasks
just check       # lint, types and tests
just up          # build and run the service in its container (compose project ramascene_jobs)
```

## License

GPL-3.0-or-later, the same as the rest of RaMa-Scene; see [LICENSE](../LICENSE).
