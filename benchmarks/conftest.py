"""Benchmark-only setup; the Django bootstrap lives in the root conftest.py.

OpenBLAS worker threads spin under CodSpeed's CPU simulation (valgrind runs
threads one at a time), so a small matrix product can take minutes. One
thread also keeps the measurements deterministic. This must happen before
numpy is first imported.
"""
import os

os.environ.setdefault("OPENBLAS_NUM_THREADS", "1")
os.environ.setdefault("OMP_NUM_THREADS", "1")
