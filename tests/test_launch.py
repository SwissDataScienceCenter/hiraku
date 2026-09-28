from __future__ import annotations

from pathlib import Path

import pytest

from hiraku.config import HirakuConfig
from hiraku.errors import ValidationError
from hiraku.launch import launch, render_arguments
from hiraku.records import Record


def test_render_arguments() -> None:
    assert render_arguments({"seed": 7, "verbose": True, "skip": False}) == (
        "--seed",
        "7",
        "--verbose",
    )


def test_launch_passes_record_identity_and_args(tmp_path: Path) -> None:
    config = HirakuConfig.init(tmp_path, install_skill=False)
    record = Record.create("baseline", config=config)
    entrypoint = {
        "command": [
            "python",
            "-c",
            "import os,sys;print('|'.join((os.environ['HIRAKU_RECORD_ID'],"
            "os.environ['HIRAKU_RECORD_FILE'],*sys.argv[1:])))",
        ],
        "args": {"seed": 1},
    }
    result = launch(
        entrypoint,
        record_id=record.id,
        args={"seed": 2},
        capture_output=True,
        config=config,
    )
    parts = result.stdout.strip().split("|")
    assert parts == [record.id, str(record.path), "--seed", "2"]


def test_launch_rejects_reserved_environment(tmp_path: Path) -> None:
    config = HirakuConfig.init(tmp_path, install_skill=False)
    Record.create("baseline", config=config)
    with pytest.raises(ValidationError, match="reserved"):
        launch(
            {"command": ["echo", "hello"]},
            record_id="baseline",
            env={"HIRAKU_RECORD_ID": "wrong"},
            config=config,
        )


def test_launch_uses_local_commit_not_inherited(tmp_path: Path) -> None:
    config = HirakuConfig.init(tmp_path, install_skill=False)
    root = Record.create("root", commit="a" * 40, config=config)
    child = Record.create("child", parent_id=root.id, config=config)
    assert child.commit is None
    assert "commit" not in child.resolve(config=config).values
