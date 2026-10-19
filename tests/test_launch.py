from __future__ import annotations

from pathlib import Path

import pytest
from test_jobs import project as project
from test_jobs import submit

from hiraku.config import HirakuConfig
from hiraku.errors import ValidationError
from hiraku.record import Record


def test_job_rejects_reserved_environment(project):
    with pytest.raises(ValidationError, match="reserved"):
        submit(project, "pass", env={"HIRAKU_RECORD_ID": "wrong"})


def test_job_uses_local_commit_not_inherited(tmp_path: Path) -> None:
    config = HirakuConfig.init(tmp_path, install_skill=False)
    root = Record.create("root", commit="a" * 40, config=config)
    child = Record.create("child", parent_id=root.id, config=config)
    assert child.commit is None
    assert "commit" not in child.resolve(config=config).values
