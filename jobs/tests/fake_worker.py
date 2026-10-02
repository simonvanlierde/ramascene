"""Stands in for `python -m ramascene_jobs.worker IN OUT` in the unit tests.

The scenario's label picks the behaviour: "fail" reports an error, "crash"
exits without output, "sleep" outlives any short timeout. Anything else holds
~200 MiB for a moment (so the parent's peak-RSS reading has something to see)
and echoes a result.
"""

import json
import sys
import time
from pathlib import Path

in_path, out_path = (Path(a) for a in sys.argv[1:])
job = json.loads(in_path.read_text())
label = job["scenario"]["label"]
if label == "fail":
    out_path.write_text(json.dumps({"error": "ValueError: boom"}))
    sys.exit(1)
if label == "crash":
    sys.exit(3)
if label == "sleep":
    time.sleep(60)
block = bytearray(200 * 2**20)
block[::4096] = b"x" * len(block[::4096])  # touch every page so it is resident
out_path.write_text(json.dumps({"results": {"baseline": {}, "scenario": {}}, "echo": job}))
