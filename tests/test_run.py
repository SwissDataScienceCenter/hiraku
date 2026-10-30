from __future__ import annotations

import math

import pytest

from hiraku.errors import ValidationError
from hiraku.run import Run


def test_run_duration_is_parsed_as_seconds() -> None:
    assert Run(id="a", duration="4.5").duration == 4.5
    assert Run(id="b").duration is None


def test_run_id_must_not_be_empty() -> None:
    with pytest.raises(ValueError, match="at least 1 character"):
        Run(id="")


@pytest.mark.parametrize("value", [-0.1, math.nan, math.inf])
def test_run_rejects_invalid_duration(value: float) -> None:
    with pytest.raises(ValidationError, match="duration"):
        Run(id="a", duration=value)
