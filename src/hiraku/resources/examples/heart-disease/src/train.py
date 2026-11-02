from __future__ import annotations

import argparse
import os
import uuid
from datetime import UTC, datetime
from pathlib import Path

import pandas as pd
from sklearn.compose import ColumnTransformer
from sklearn.ensemble import RandomForestClassifier
from sklearn.impute import SimpleImputer
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import accuracy_score, log_loss
from sklearn.model_selection import train_test_split
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import OneHotEncoder, StandardScaler

COLUMNS = [
    "age",
    "sex",
    "cp",
    "trestbps",
    "chol",
    "fbs",
    "restecg",
    "thalach",
    "exang",
    "oldpeak",
    "slope",
    "ca",
    "thal",
    "num",
]
CATEGORICAL = ["sex", "cp", "fbs", "restecg", "exang", "slope", "thal"]
NUMERIC = [column for column in COLUMNS[:-1] if column not in CATEGORICAL]
RUN_FIELDS = [
    "id",
    "record_id",
    "commit",
    "git_state",
    "timestamp",
    "duration",
    "param.model",
    "param.seed",
    "param.validation_fraction",
    "metric.val/log_loss",
    "metric.val/accuracy",
]


def hiraku_csv_fields() -> dict[str, str]:
    """Return available Hiraku launch attribution for a CSV row."""
    if not os.environ.get("HIRAKU_RECORD_ID"):
        return {}
    return {
        field: value
        for field in ("record_id", "commit", "git_state")
        if (value := os.environ.get(f"HIRAKU_{field.upper()}"))
        and not (field == "git_state" and value == "unavailable")
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--model", choices=["logistic", "random_forest"], required=True)
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--validation-fraction", type=float, default=0.25)
    return parser.parse_args()


def build_model(model_name: str, seed: int) -> Pipeline:
    preprocessing = ColumnTransformer(
        [
            (
                "numeric",
                Pipeline(
                    [
                        ("impute", SimpleImputer(strategy="median")),
                        ("scale", StandardScaler()),
                    ]
                ),
                NUMERIC,
            ),
            (
                "categorical",
                Pipeline(
                    [
                        ("impute", SimpleImputer(strategy="most_frequent")),
                        ("encode", OneHotEncoder(handle_unknown="ignore")),
                    ]
                ),
                CATEGORICAL,
            ),
        ]
    )
    classifier = (
        LogisticRegression(max_iter=2000, random_state=seed)
        if model_name == "logistic"
        else RandomForestClassifier(n_estimators=300, random_state=seed, n_jobs=1)
    )
    return Pipeline([("preprocess", preprocessing), ("classifier", classifier)])


def append_run(path: Path, row: dict[str, str]) -> None:
    if path.exists() and path.stat().st_size:
        header = pd.read_csv(path, nrows=0).columns.tolist()
        if header != RUN_FIELDS:
            raise RuntimeError(f"Unexpected runs CSV header in {path}: {header!r}")
    path.parent.mkdir(parents=True, exist_ok=True)
    pd.DataFrame([row], columns=RUN_FIELDS).to_csv(
        path,
        mode="a",
        header=not path.exists() or path.stat().st_size == 0,
        index=False,
    )


def main() -> None:
    started = datetime.now(UTC)
    args = parse_args()
    frame = pd.read_csv(
        Path("data/processed.cleveland.data"), names=COLUMNS, na_values="?"
    )
    features = frame.drop(columns="num")
    target = (frame["num"] > 0).astype(int)
    train_x, validation_x, train_y, validation_y = train_test_split(
        features,
        target,
        test_size=args.validation_fraction,
        random_state=args.seed,
        stratify=target,
    )
    model = build_model(args.model, args.seed)
    model.fit(train_x, train_y)
    probabilities = model.predict_proba(validation_x)[:, 1]
    predictions = (probabilities >= 0.5).astype(int)
    metrics = {
        "log_loss": log_loss(validation_y, probabilities),
        "accuracy": accuracy_score(validation_y, predictions),
    }
    runs_path = Path(os.environ.get("HIRAKU_TRACKING_ROOT", ".")) / "runs.csv"
    append_run(
        runs_path,
        {
            "id": uuid.uuid4().hex,
            **hiraku_csv_fields(),
            "timestamp": started.isoformat(),
            "duration": f"{(datetime.now(UTC) - started).total_seconds():.3f}",
            "param.model": args.model,
            "param.seed": str(args.seed),
            "param.validation_fraction": str(args.validation_fraction),
            "metric.val/log_loss": f"{metrics['log_loss']:.8f}",
            "metric.val/accuracy": f"{metrics['accuracy']:.8f}",
        },
    )


if __name__ == "__main__":
    main()
