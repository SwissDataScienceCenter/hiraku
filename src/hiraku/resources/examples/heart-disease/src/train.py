from __future__ import annotations

import argparse
import csv
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

from hiraku.config import HirakuConfig
from hiraku.jobs import current_job

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
    "job_id",
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


def provenance() -> dict[str, str]:
    job = current_job()
    if job is None:
        raise RuntimeError("Run this example through Hiraku to record job attribution")
    config = HirakuConfig.discover()
    return {
        "HIRAKU_JOB_ID": job.id,
        "HIRAKU_RECORD_ID": job.record_id,
        "HIRAKU_COMMIT": job.commit or "",
        "HIRAKU_GIT_STATE": job.git_state,
        "HIRAKU_ROOT": str(config.root),
        "HIRAKU_TRACKING_ROOT": str(config.root),
    }


def append_run(path: Path, row: dict[str, str]) -> None:
    if path.exists() and path.stat().st_size:
        with path.open(newline="", encoding="utf-8") as stream:
            reader = csv.DictReader(stream)
            header = reader.fieldnames
            existing = list(reader)
        if header is None or "id" not in header:
            raise RuntimeError(f"Unexpected runs CSV header in {path}: {header!r}")
        expanded_header = list(dict.fromkeys([*header, *RUN_FIELDS]))
        if expanded_header != header:
            with path.open("w", newline="", encoding="utf-8") as stream:
                writer = csv.DictWriter(stream, fieldnames=expanded_header)
                writer.writeheader()
                writer.writerows(existing)
    path.parent.mkdir(parents=True, exist_ok=True)
    write_header = not path.exists() or path.stat().st_size == 0
    with path.open("a", newline="", encoding="utf-8") as stream:
        with path.open(newline="", encoding="utf-8") as source:
            header = next(csv.reader(source), None)
        fields = list(dict.fromkeys([*(header or []), *RUN_FIELDS]))
        writer = csv.DictWriter(stream, fieldnames=fields)
        if write_header:
            writer.writeheader()
        writer.writerow(row)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--model", choices=["logistic", "random_forest"], required=True)
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--validation-fraction", type=float, default=0.25)
    return parser.parse_args()


def main() -> None:
    started = datetime.now(UTC)
    args = parse_args()
    attribution = provenance()
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
    runs_path = Path(attribution["HIRAKU_TRACKING_ROOT"]) / "runs.csv"
    append_run(
        runs_path,
        {
            "id": uuid.uuid4().hex,
            "job_id": attribution["HIRAKU_JOB_ID"],
            "record_id": attribution["HIRAKU_RECORD_ID"],
            "commit": attribution["HIRAKU_COMMIT"],
            "git_state": attribution["HIRAKU_GIT_STATE"],
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
