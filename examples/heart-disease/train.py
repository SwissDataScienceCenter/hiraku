from __future__ import annotations

import json
import os
from pathlib import Path

from sklearn.datasets import load_breast_cancer
from sklearn.ensemble import RandomForestClassifier
from sklearn.linear_model import LogisticRegression
from sklearn.model_selection import StratifiedKFold, cross_val_score
from sklearn.pipeline import make_pipeline
from sklearn.preprocessing import StandardScaler

experiment = os.environ["HIRAKU_EXPERIMENT_ID"]
features, labels = load_breast_cancer(return_X_y=True)
folds = StratifiedKFold(n_splits=5, shuffle=True, random_state=42)
config_path = Path("model-config.json")
configured_model = json.loads(config_path.read_text(encoding="utf-8"))["model"] if config_path.exists() else None

if os.environ["HIRAKU_EXPERIMENT_KIND"] == "baseline" and configured_model is None:
    model = make_pipeline(StandardScaler(), LogisticRegression(max_iter=2_000, random_state=42))
    model_name = "logistic"
else:
    model = RandomForestClassifier(n_estimators=150, random_state=42, n_jobs=1)
    model_name = "random-forest"

scores = cross_val_score(model, features, labels, cv=folds, scoring="roc_auc")
Path(os.environ["HIRAKU_METRICS_PATH"]).write_text(
    json.dumps(
        {
            "parameters": {"seed": 42, "model": model_name, "folds": 5},
            "metrics": {
                "roc_auc_mean": float(scores.mean()),
                "roc_auc_std": float(scores.std()),
            },
        }
    ),
    encoding="utf-8",
)
