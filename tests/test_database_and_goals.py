from __future__ import annotations

import pytest

from hiraku.database import SCHEMA_VERSION
from hiraku.errors import HirakuError


def test_init_and_goal_use_sqlite_with_manifest_projection(project):
    root, app = project
    assert (root / ".hiraku" / "state.db").exists()
    assert not (root / ".hiraku" / "state.db").read_bytes().startswith(b"---")
    goal = app.goals.get("demo")
    assert goal.metric == "score"
    assert goal.direction == "max"
    assert goal.launch_manifest["entrypoints"]["evaluate"]["command"] == ["python", "evaluate.py"]
    guided = (root / ".agents" / "skills" / "hiraku" / "guided.md").read_text(encoding="utf-8")
    assert "explicit user approval" in guided
    with app.db.connect() as connection:
        assert connection.execute("PRAGMA foreign_keys").fetchone()[0] == 1
        assert connection.execute("SELECT version FROM schema_version").fetchone()[0] == SCHEMA_VERSION


def test_goal_metric_is_immutable_after_runs(project):
    _, app = project
    experiment = app.experiments.create("demo/baseline", "reference", kind="baseline")
    run_id = app.runs.create(experiment, app.git.head_commit(), "evaluate", {})
    app.runs.finalize(run_id, "completed", 0)
    with pytest.raises(HirakuError, match="cannot change"):
        app.goals.update("demo", metric="other")
    with pytest.raises(HirakuError, match="cannot change"):
        app.goals.update("demo", evaluation_protocol="different")


def test_transaction_rolls_back(project):
    _, app = project
    with pytest.raises(RuntimeError), app.db.transaction() as connection:
        connection.execute(
            "INSERT INTO events(entity_type,entity_id,event_type,payload_json,created_at) "
            "VALUES ('test','x','started','{}','now')"
        )
        raise RuntimeError("stop")
    with app.db.connect() as connection:
        assert connection.execute("SELECT COUNT(*) FROM events WHERE entity_type='test'").fetchone()[0] == 0


def test_invalid_manifest_is_concise(project):
    root, app = project
    bad = root / "bad.yml"
    bad.write_text("entrypoints:\n  x:\n    command: echo nope\n", encoding="utf-8")
    # Make the manifest tracked so cleanliness does not mask validation.
    app.git._run("add", "bad.yml")
    app.git._run("commit", "-m", "bad manifest fixture")
    with pytest.raises(HirakuError, match="string list"):
        app.goals.create("bad", "score", "max", bad)
