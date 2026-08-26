from __future__ import annotations

import argparse
import csv
import hashlib
import os
import urllib.request
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

DATA_URL = (
    "https://archive.ics.uci.edu/ml/machine-learning-databases/"
    "heart-disease/processed.cleveland.data"
)
DATA_SHA256 = "a74b7efa387bc9d108d7d0115d831fe9b414b29ae7124f331b622b4efa0427c8"
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


def obtain_data(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if not path.exists():
        urllib.request.urlretrieve(DATA_URL, path)
    digest = hashlib.sha256(path.read_bytes()).hexdigest()
    if digest != DATA_SHA256:
        raise RuntimeError(f"Dataset checksum mismatch: expected {DATA_SHA256}, received {digest}")


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


def append_run(path: Path, values: dict[str, object]) -> None:
    fields = [
        "run_id",
        "task_id",
        "experiment_id",
        "commit",
        "created_at",
        "model",
        "seed",
        "validation_fraction",
        "val/log_loss",
        "val/accuracy",
    ]
    write_header = not path.exists() or path.stat().st_size == 0
    with path.open("a", encoding="utf-8", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=fields)
        if write_header:
            writer.writeheader()
        writer.writerow(values)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--model", choices=["logistic", "random_forest"], required=True)
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--validation-fraction", type=float, default=0.25)
    args = parser.parse_args()

    data_path = Path("data/processed.cleveland.data")
    obtain_data(data_path)
    frame = pd.read_csv(data_path, names=COLUMNS, na_values="?")
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

    table_path = Path(os.environ["HIRAKU_TABLE_PATH"])
    table_path.parent.mkdir(parents=True, exist_ok=True)
    append_run(
        table_path,
        {
            "run_id": uuid.uuid4().hex,
            "task_id": os.environ["HIRAKU_TASK_ID"],
            "experiment_id": os.environ["HIRAKU_EXPERIMENT_ID"],
            "commit": os.environ["HIRAKU_SOURCE_COMMIT"],
            "created_at": datetime.now(UTC).isoformat().replace("+00:00", "Z"),
            "model": args.model,
            "seed": args.seed,
            "validation_fraction": args.validation_fraction,
            "val/log_loss": f"{log_loss(validation_y, probabilities):.8f}",
            "val/accuracy": f"{accuracy_score(validation_y, predictions):.8f}",
        },
    )


if __name__ == "__main__":
    main()
