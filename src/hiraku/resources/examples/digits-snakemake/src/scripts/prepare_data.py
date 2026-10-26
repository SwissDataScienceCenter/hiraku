"""Materialize the split Digits dataset as an explicit workflow input."""

from __future__ import annotations

import argparse
import os
from pathlib import Path

import numpy as np
from sklearn.datasets import load_digits
from sklearn.model_selection import train_test_split


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--validation-fraction", type=float, default=0.25)
    args = parser.parse_args()
    if not 0 < args.validation_fraction < 1:
        parser.error("validation-fraction must be between zero and one")
    return args


def save_atomically(path: Path, **arrays: np.ndarray) -> None:
    """Replace ``path`` only after the complete archive was written."""
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.tmp")
    try:
        with temporary.open("wb") as stream:
            np.savez(stream, **arrays)
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def main() -> None:
    args = parse_args()
    data = load_digits()
    # Pixels have a known range of 0..16; no statistics are fit on validation data.
    train_x, val_x, train_y, val_y = train_test_split(
        data.data / 16.0,
        data.target,
        test_size=args.validation_fraction,
        random_state=args.seed,
        stratify=data.target,
    )
    save_atomically(
        args.output,
        train_x=train_x.astype(np.float32),
        train_y=train_y.astype(np.int64),
        val_x=val_x.astype(np.float32),
        val_y=val_y.astype(np.int64),
    )
    print(
        f"Wrote {args.output}: {len(train_y)} train and {len(val_y)} validation samples"
    )


if __name__ == "__main__":
    main()
