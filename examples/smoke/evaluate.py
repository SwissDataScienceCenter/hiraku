from __future__ import annotations

import json
import os
import sys
from pathlib import Path

if "--fail" in sys.argv:
    raise SystemExit(7)

score = 0.80 if os.environ["HIRAKU_EXPERIMENT_KIND"] == "baseline" else 0.90
Path(os.environ["HIRAKU_METRICS_PATH"]).write_text(
    json.dumps({"parameters": {"seed": 42}, "metrics": {"score": score}}),
    encoding="utf-8",
)
