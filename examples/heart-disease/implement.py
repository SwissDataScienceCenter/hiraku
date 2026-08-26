from __future__ import annotations

import json
import os
from pathlib import Path

Path("model-config.json").write_text(
    json.dumps(
        {
            "experiment": os.environ["HIRAKU_EXPERIMENT_ID"],
            "model": "random-forest",
            "seed": 42,
        },
        indent=2,
    )
    + "\n",
    encoding="utf-8",
)
