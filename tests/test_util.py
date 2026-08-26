from __future__ import annotations

from pathlib import Path

import pytest

from hiraku.errors import ValidationError
from hiraku.tracking import sort_runs
from hiraku.util import parse_invocation, safe_relative, validate_id


def test_parse_invocation_uses_quoting_without_shell_execution() -> None:
    assert parse_invocation('train --label "two words"') == ["train", "--label", "two words"]
    assert parse_invocation("train '$(touch nope)'") == ["train", "$(touch nope)"]


@pytest.mark.parametrize("value", ["", "UPPER", "white space", "/root", "a:b"])
def test_invalid_ids(value: str) -> None:
    with pytest.raises(ValidationError):
        validate_id(value, "task")


def test_safe_relative_rejects_escape(tmp_path: Path) -> None:
    assert safe_relative(tmp_path, "nested/file", "test") == tmp_path / "nested" / "file"
    with pytest.raises(ValidationError):
        safe_relative(tmp_path, "../escape", "test")


def test_sort_runs_ranks_numeric_and_retains_other_rows() -> None:
    rows = [
        {"run_id": "a", "score": "2"},
        {"run_id": "b", "score": "missing"},
        {"run_id": "c", "score": "1"},
        {"run_id": "d", "score": ""},
    ]
    result = sort_runs(rows, "score", descending=False)
    assert [row["run_id"] for row in result] == ["c", "a", "b", "d"]
    assert [row.get("_rank") for row in result] == [1, 2, None, None]
