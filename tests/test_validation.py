from __future__ import annotations

import pytest

from hiraku.errors import ValidationError
from hiraku.execution import validate_command


def test_validate_command_accepts_none_and_copies_argv_sequences() -> None:
    assert validate_command(None) is None

    command = ("python", "train.py")
    validated = validate_command(command)

    assert validated == ["python", "train.py"]
    assert validated is not command


@pytest.mark.parametrize(
    ("value", "message"),
    [
        ("python train.py", "argv sequence"),
        (b"python", "argv sequence"),
        ({}, "argv sequence"),
        ([], "non-empty string"),
    ],
)
def test_validate_command_rejects_non_argv_values(value: object, message: str) -> None:
    with pytest.raises(ValidationError, match=message):
        validate_command(value)


@pytest.mark.parametrize("value", [[""], [1], ["python", None], ["python", "x\x00y"]])
def test_validate_command_rejects_invalid_arguments(value: list[object]) -> None:
    with pytest.raises(ValidationError):
        validate_command(value)
