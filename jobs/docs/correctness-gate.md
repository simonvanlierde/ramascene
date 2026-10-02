# Correctness gate: recorded run

`uv run pytest -m engine -s` on a 32-core Linux machine (i9-14900KF), 2 October
2026, with `DATASETS_DIR` pointing at the EXIOBASE 2011 `_v4` matrices and
`DATASETS_VERSION=v4`. BLAS used all cores and other work was running on the
machine, so the times are indicative. Reference: the explorer's committed
`data/results.json`.

Run once with the engine before the scenario speed-ups (packaging on top of the
solve-instead-of-invert change) and once with this checkout's engine, which
updates the published L for a scenario instead of factorizing `I - A`. Peak RSS
is the job process's `ru_maxrss`; with the newer engine most of it is the
memory-mapped A and L, which other processes share.

| Job | Result | Older engine | This engine |
| --- | --- | --- | --- |
| Explorer's steel -20% scenario (its `model_details`, verbatim) | scenario matches `data/results.json` to 6.3e-15 (older) and 5.3e-15 (this) relative; baseline identical | 23.3 s | 1.4-1.6 s, peak RSS 1.53 GiB |
| The same cells, 0% change, `reference_check: true` | all 122 Octave reference values within 1e-6 (max relative difference 4.2e-8) | 36.6 s, peak RSS 1.57 GiB | 1.7-1.9 s, peak RSS 1.53 GiB |

Both runs: `2 passed`.
