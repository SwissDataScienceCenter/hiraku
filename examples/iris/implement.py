from __future__ import annotations

import json
import os
from pathlib import Path

Path("iris-model.json").write_text(
    json.dumps(
        {
            "model": "logistic-regression",
            "experiment": os.environ["HIRAKU_EXPERIMENT_ID"],
            "hypothesis": os.environ["HIRAKU_HYPOTHESIS"],
        },
        indent=2,
    )
    + "\n",
    encoding="utf-8",
)
