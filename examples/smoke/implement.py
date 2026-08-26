from __future__ import annotations

import json
import os
from pathlib import Path

Path("experiment-config.json").write_text(
    json.dumps(
        {
            "experiment": os.environ["HIRAKU_EXPERIMENT_ID"],
            "hypothesis": os.environ["HIRAKU_HYPOTHESIS"],
            "strategy": "bounded-alternative",
        },
        indent=2,
    )
    + "\n",
    encoding="utf-8",
)
