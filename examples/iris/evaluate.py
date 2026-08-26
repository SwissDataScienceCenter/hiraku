from __future__ import annotations

import json
import os
from pathlib import Path

from sklearn.datasets import load_iris
from sklearn.dummy import DummyClassifier
from sklearn.linear_model import LogisticRegression
from sklearn.model_selection import StratifiedKFold, cross_val_score
from sklearn.pipeline import make_pipeline
from sklearn.preprocessing import StandardScaler

features, labels = load_iris(return_X_y=True)
folds = StratifiedKFold(n_splits=5, shuffle=True, random_state=42)
config_path = Path("iris-model.json")
configured_model = json.loads(config_path.read_text(encoding="utf-8"))["model"] if config_path.exists() else None

if os.environ["HIRAKU_EXPERIMENT_KIND"] == "baseline" and configured_model is None:
    model = DummyClassifier(strategy="most_frequent")
    model_name = "majority-baseline"
else:
    model = make_pipeline(StandardScaler(), LogisticRegression(max_iter=2_000, random_state=42))
    model_name = "logistic-regression"

scores = cross_val_score(model, features, labels, cv=folds, scoring="accuracy")
Path(os.environ["HIRAKU_METRICS_PATH"]).write_text(
    json.dumps(
        {
            "parameters": {"seed": 42, "folds": 5, "model": model_name},
            "metrics": {
                "accuracy_mean": float(scores.mean()),
                "accuracy_std": float(scores.std()),
            },
        }
    ),
    encoding="utf-8",
)
