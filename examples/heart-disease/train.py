from __future__ import annotations

import argparse
import csv
import hashlib
import os
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

import pandas as pd
from sklearn.compose import ColumnTransformer
from sklearn.ensemble import RandomForestClassifier
from sklearn.impute import SimpleImputer
from sklearn.linear_model import LogisticRegression
from sklearn.model_selection import StratifiedKFold, cross_validate
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import OneHotEncoder, StandardScaler

DATA_PATH = Path(__file__).parent / "data" / "processed.cleveland.data"
DATASET_SHA256 = "a74b7efa387bc9d108d7d0115d831fe9b414b29ae7124f331b622b4efa0427c8"
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
NUMERIC = ["age", "trestbps", "chol", "thalach", "oldpeak"]
CATEGORICAL = [column for column in COLUMNS[:-1] if column not in NUMERIC]
CV_FOLDS = 5
CV_SEED = 42
RESULT_FIELDS = [
    "experiment_id",
    "commit",
    "run_id",
    "timestamp_utc",
    "model",
    "dataset_sha256",
    "cv_seed",
    "cv_folds",
    "roc_auc_mean",
    "roc_auc_std",
    "accuracy_mean",
    "accuracy_std",
]


def verify_and_load_data() -> tuple[pd.DataFrame, pd.Series]:
    digest = hashlib.sha256(DATA_PATH.read_bytes()).hexdigest()
    if digest != DATASET_SHA256:
        raise RuntimeError(f"Dataset checksum mismatch: expected {DATASET_SHA256}, found {digest}")
    frame = pd.read_csv(DATA_PATH, names=COLUMNS, na_values="?")
    if frame.shape != (303, 14):
        raise RuntimeError(f"Expected dataset shape (303, 14), found {frame.shape}")
    target_values = set(frame["num"].dropna().astype(int).unique())
    if target_values != {0, 1, 2, 3, 4}:
        raise RuntimeError(f"Unexpected target domain: {sorted(target_values)}")
    return frame.drop(columns="num"), (frame["num"] > 0).astype(int)


def make_pipeline(model_name: str) -> Pipeline:
    numeric = Pipeline(
        [
            ("impute", SimpleImputer(strategy="median")),
            ("scale", StandardScaler()),
        ]
    )
    categorical = Pipeline(
        [
            ("impute", SimpleImputer(strategy="most_frequent")),
            ("encode", OneHotEncoder(handle_unknown="ignore", sparse_output=False)),
        ]
    )
    preprocess = ColumnTransformer(
        [("numeric", numeric, NUMERIC), ("categorical", categorical, CATEGORICAL)]
    )
    if model_name == "logistic":
        model = LogisticRegression(max_iter=2000, solver="liblinear", random_state=CV_SEED)
    elif model_name == "random-forest":
        model = RandomForestClassifier(
            n_estimators=300,
            min_samples_leaf=2,
            class_weight="balanced",
            n_jobs=1,
            random_state=CV_SEED,
        )
    else:  # pragma: no cover - argparse enforces this
        raise ValueError(f"Unknown model: {model_name}")
    return Pipeline([("preprocess", preprocess), ("model", model)])


def append_result(path: Path, row: dict[str, str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    exists = path.exists()
    if exists:
        with path.open(encoding="utf-8", newline="") as stream:
            header = next(csv.reader(stream), None)
        if header != RESULT_FIELDS:
            raise RuntimeError(f"Existing run table has an incompatible header: {header}")
    with path.open("a", encoding="utf-8", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=RESULT_FIELDS)
        if not exists:
            writer.writeheader()
        writer.writerow(row)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--model", choices=["logistic", "random-forest"], required=True)
    args = parser.parse_args()
    features, target = verify_and_load_data()
    cv = StratifiedKFold(n_splits=CV_FOLDS, shuffle=True, random_state=CV_SEED)
    scores = cross_validate(
        make_pipeline(args.model),
        features,
        target,
        cv=cv,
        scoring={"roc_auc": "roc_auc", "accuracy": "accuracy"},
        n_jobs=1,
    )
    append_result(
        Path(os.environ["HIRAKU_TABLE_PATH"]),
        {
            "experiment_id": os.environ["HIRAKU_EXPERIMENT_ID"],
            "commit": os.environ["HIRAKU_GIT_COMMIT"],
            "run_id": str(uuid4()),
            "timestamp_utc": datetime.now(UTC).isoformat(),
            "model": args.model,
            "dataset_sha256": DATASET_SHA256,
            "cv_seed": str(CV_SEED),
            "cv_folds": str(CV_FOLDS),
            "roc_auc_mean": f"{scores['test_roc_auc'].mean():.6f}",
            "roc_auc_std": f"{scores['test_roc_auc'].std():.6f}",
            "accuracy_mean": f"{scores['test_accuracy'].mean():.6f}",
            "accuracy_std": f"{scores['test_accuracy'].std():.6f}",
        },
    )


if __name__ == "__main__":
    main()
