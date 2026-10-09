from __future__ import annotations

import csv
import runpy
from pathlib import Path

import pytest

from hiraku.config import HirakuConfig
from hiraku.dashboard import build_snapshot

SCRIPT = Path(__file__).resolve().parents[1] / "scripts/test_dashboard.py"
helpers = runpy.run_path(str(SCRIPT))


def test_demo_has_distinct_scopes_and_refresh_rows(tmp_path):
    directory = helpers["create_demo"](tmp_path / "demo")
    config = HirakuConfig.load(directory / "hiraku.yml")
    assert len(build_snapshot(config, record_id="child-a").runs) == 2
    assert len(build_snapshot(config, record_id="child-a", scope="source").runs) == 3
    assert build_snapshot(config, record_id="empty").runs == ()
    assert not (directory / "results/empty.csv").exists()
    assert config.active_record_id is None
    assert helpers["append_run"](directory) == "refreshed-1"
    assert helpers["append_run"](directory) == "refreshed-2"
    assert len(build_snapshot(config, record_id="child-a").runs) == 4
    with (directory / "results/a.csv").open(newline="") as stream:
        assert len(list(csv.DictReader(stream))) == 5


def test_demo_refuses_to_overwrite_files(tmp_path):
    important = tmp_path / "keep.txt"
    important.write_text("keep")
    with pytest.raises(ValueError, match="must be empty"):
        helpers["create_demo"](tmp_path)
    assert important.read_text() == "keep"


def test_append_refuses_non_demo_project(tmp_path):
    config = HirakuConfig.init(tmp_path, install_skill=False)
    with pytest.raises(ValueError, match="Not a dashboard demo"):
        helpers["append_run"](tmp_path)
    assert config.path.is_file()
    assert not (tmp_path / "results/a.csv").exists()


def test_append_refuses_changed_csv_schema(tmp_path):
    directory = helpers["create_demo"](tmp_path / "demo")
    csv_path = directory / "results/a.csv"
    csv_path.write_text("custom,columns\na,b\n")
    before = csv_path.read_bytes()
    with pytest.raises(ValueError, match="Unexpected demo CSV columns"):
        helpers["append_run"](directory)
    assert csv_path.read_bytes() == before
