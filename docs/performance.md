# Performance

DRAFT. How fast a scenario job is, and what made it so. Every number says what
it measured and where it comes from. The 2011 EXIOBASE v3.3.sm matrices are
9800 x 9800 (49 countries x 200 products); A and L are 733 MB each on disk.

## From inverting to updating

One technical-coefficient scenario job, peak memory and time per job:

| Engine | Peak RSS | Time per job | Source |
| --- | --- | --- | --- |
| Inverts I - A (`master`) | 3.97 GB | 29.8 s | peak RSS: the explorer's `data/results.json` of 2026-09-26, `provenance.peak_rss_bytes` = 3972194304; time: PR #74, 1 BLAS thread |
| Solves (I - A) x = y instead | 1.55 GiB (1,621,328 KiB) | 7.9 s | the explorer's grid run with `perf/solve-not-invert`; time: PR #74 |
| Updates the published L (Woodbury) | 1.5 GiB | 0.28 s | PR #74: median of 4 to 5 jobs, 1 BLAS thread |

A scenario changes only a block of A, so the Woodbury update needs one
product with L and a small solve instead of a factorization of the whole
matrix. In the last row almost all of the 1.5 GiB is the A and L files mapped
into memory and shared between workers. The job's own memory is about 60 MB.

## The job API

`jobs/` runs each scenario through the API in its own worker process. Times
below are per job through the real API, on a workstation with an Intel Core
i9-14900KF (24 cores, 32 threads), numpy's default BLAS threads, 15 jobs of
each kind interleaved across the variants. Median, with the range in brackets.
The machine was shared with other work (load average 5 to 19 during the run),
so compare the rows with each other, not with other machines.

"Steel" is the explorer's gate scenario: steel into European motor vehicles,
-20%, a technical-coefficient change (Woodbury). "Demand" is European
final demand for motor vehicles, -20%, a final-demand change.

| Variant | Steel: worker | Steel: wall | Demand: worker | Demand: wall | Peak RSS (steel / demand) |
| --- | --- | --- | --- | --- | --- |
| Before: each job hashes the four dataset files | 1.68 s [1.54-1.79] | 1.78 s [1.65-1.90] | 1.46 s [1.28-1.55] | 1.54 s [1.36-1.65] | 1.52 / 0.82 GiB |
| The API hashes them once per process | 0.84 s [0.75-1.07] | 1.05 s [0.86-1.34] | 0.75 s [0.63-0.82] | 0.84 s [0.72-0.91] | 1.52 / 0.82 GiB |
| Same, but L and B read into memory instead of mapped | 1.07 s [0.82-1.18] | 1.24 s [0.90-1.50] | 0.77 s [0.71-0.98] | 0.88 s [0.79-1.08] | 1.52 / 0.83 GiB |

"Worker" is the worker process's wall time as the API measures it; "wall" is
from submitting the job to reading its result. Peak RSS is the worker's, from
`wait4`.

- Hashing the four files took 0.65 to 0.74 s of every job. The API now hashes
  them before the first job and again only when a file's size or modification
  time changes. That halves a job.
- Mapping L and B (the engine reads them with `mmap_mode="r"`) saves about
  0.23 s on a steel job, mostly in the baseline (0.26 s against 0.14 s). The
  unmapped variant also carried the L.dot memo described below, which saves
  less than 0.03 s. Mapping does not lower the measured peak RSS: mapped
  pages a process has read count in its RSS. They are shared between workers,
  though, so the memory the machine needs per extra worker is far below
  1.5 GiB.
- Of the 0.84 s a steel job now takes, the scenario is 0.41 s and the
  baseline 0.14 s. The rest, about 0.3 s, is starting Python, importing
  numpy, scipy and Django, setting up Django and writing the 3.6 MB result.
- Remembering L.dot(y) across the three indicators of a run (the routes ask
  for the same product three times) saved under 0.03 s per job, inside the
  spread, so it was left out.

### The response

A job's result holds the embodied flows: two arrays of 3 x 49 x 15 x 49 =
108,045 values. For the steel job:

| Encoding | Raw JSON | Gzipped | Passes the gate |
| --- | --- | --- | --- |
| float64, baseline and scenario absolute (current) | 3.59 MB | 1.65 MB | yes, worst cell 6.0e-8 |
| float32, both absolute | 2.09 MB | 0.88 MB | no: the change in each indicator's total is off by 5e-5 to 6e-5 relative |
| float32 baseline + float32 change | 2.28 MB | 0.91 MB | yes, worst cell 6.0e-8 |
| float64, baseline + changed cells only | 4.05 MB | 1.85 MB | yes |

83% of the cells change (89,621 of 108,045), because a change anywhere moves
output everywhere through L, so sending only the changed cells is larger. If
the format changes, send the scenario as float32 changes from the baseline,
the way the explorer's `embodied.parquet` stores it: 45% fewer gzipped bytes.
On its own that saves about 0.75 MB per job, so it is not worth a change to
the explorer alone.

## A GPU does not pay

An LU solve of an EXIOBASE-sized system ((I - A) x = y, n = 9800, 49
right-hand sides, float64) took 1.82 s on the CPU and 0.62 s on an RTX 4090
(2026-10-08, the same workstation, PyTorch on the GPU, numpy's default
threads). The GPU is 3x faster on a factorization, but a scenario job no
longer factorizes: the Woodbury update takes 0.28 s, and float32, where the
GPU is much faster, fails the Octave tolerance. So there is no GPU path.

The 1.82 s and 0.62 s are not anchored: that run did not record the CPU's
BLAS library or thread count. A rerun on 2026-10-21 on the same workstation
(Intel Core i9-14900KF, 8 performance and 16 efficiency cores, 32 threads;
scipy 1.18.1 with OpenBLAS 0.3.34, scipy-openblas build; CuPy 14.2 with
cuSOLVER on the GPU; median of 5) did not reproduce them, because another
process kept the GPU at 100% and the CPU was not idle (load average 5 to 15):

| BLAS threads | CPU solve | GPU solve | GPU solve with host copies |
| --- | --- | --- | --- |
| 8 | 3.73 s | 1.20 s | 1.29 s |
| 16 | 3.01 s | 1.23 s | 1.34 s |
| 32 | 5.13 s | 1.23 s | 1.34 s |

The GPU stays 2.5 to 4 times faster, and the CPU and GPU solutions agree to
6e-15 relative, so the conclusion holds. On this hybrid CPU more BLAS threads
than performance cores can be slower: 32 threads took 5.1 s, 16 took 3.0 s.
Both absolute numbers need a rerun on an idle machine before they are quoted.

## Benchmarks

CodSpeed measures the hot paths on every pull request
(`.github/workflows/codspeed.yml`), on synthetic inputs of the real shapes:
the worker's embodied flows and dataset hash (`jobs/tests/benchmarks`), and
the engine's scenario solve, Woodbury and full factorization
(`ramascene/tests/test_bench_engine.py`).
