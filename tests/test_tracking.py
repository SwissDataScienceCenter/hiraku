"""The MLflow mirror: what gets resolved, what gets sent, and what happens when it breaks.

No test here talks to an MLflow server. A stub module is installed into `sys.modules`, so what is
under test is the part this package owns: resolution and its narrowing rule, the params/metrics
split, the id that comes back into the row, and — the one that matters most — that a broken mirror
never costs you the run.
"""

from __future__ import annotations

import sys
import types
from pathlib import Path

import pytest

from hiraku_auto import tracking
from hiraku_auto.errors import HirakuError
from hiraku_auto.records import read_record
from hiraku_auto.runs import PROVENANCE_COLUMNS, read_runs


def set_config(project, block: str) -> None:
    """Replace the `tracking:` front-matter block in research/config.md."""
    record = read_record(project.config_md)
    import hiraku_auto.yamlio as yamlio

    record.front["tracking"] = yamlio.load(block)["tracking"]
    project.config_md.write_text(record.render(), encoding="utf-8")


# --------------------------------------------------------------------------- a stub mlflow


class StubMlflow:
    """Records what it was asked to log, and can be told to fail."""

    def __init__(self, fail: type[Exception] | None = None) -> None:
        self.uri = None
        self.experiment = None
        self.params: dict = {}
        self.metrics: dict = {}
        self.tags: dict = {}
        self.run_name = None
        self.fail = fail

    # the surface tracking.py actually uses
    def set_tracking_uri(self, uri):  self.uri = uri
    def set_experiment(self, name):   self.experiment = name
    def set_tags(self, tags):         self.tags.update(tags)
    def log_params(self, params):     self.params.update(params)
    def log_metrics(self, metrics):   self.metrics.update(metrics)

    def start_run(self, run_name=None):
        if self.fail:
            raise self.fail("server unreachable")
        self.run_name = run_name
        stub = self

        class Ctx:
            info = types.SimpleNamespace(run_id="mlf-abc123")

            def __enter__(self_inner):
                return self_inner

            def __exit__(self_inner, *exc):
                stub.closed = True
                return False

        return Ctx()

    def MlflowClient(self, tracking_uri=None):  # noqa: N802 - matches the real API
        if self.fail:
            raise self.fail("server unreachable")
        return types.SimpleNamespace(search_experiments=lambda max_results=1: [])


@pytest.fixture()
def stub(monkeypatch):
    module = StubMlflow()
    monkeypatch.setitem(sys.modules, "mlflow", module)
    return module


# --------------------------------------------------------------------------- resolution


def test_the_default_is_the_table_alone(project):
    resolved = tracking.resolve(project, "candidate", goal_id="vo-backend")
    assert resolved.backend == "table"
    assert not resolved.mirrors


def test_a_project_turns_the_mirror_on(project):
    set_config(project, """
tracking:
  backend: table+mlflow
  mlflow:
    tracking_uri: http://mlflow.example.org:5000
""")
    resolved = tracking.resolve(project, "candidate", goal_id="vo-backend")
    assert resolved.mirrors
    assert resolved.tracking_uri == "http://mlflow.example.org:5000"
    # A goal is an MLflow experiment: same idea, one set of comparable runs.
    assert resolved.experiment_name == "vo-backend"
    assert resolved.tags["hiraku.goal"] == "vo-backend"
    assert resolved.tags["hiraku.experiment_id"] == "candidate"


def test_a_goal_may_name_its_own_mlflow_experiment(project):
    set_config(project, "tracking:\n  backend: table+mlflow\n")
    record = read_record(project.goals_dir / "vo-backend.md")
    record.front["mlflow_experiment"] = "natix-vo-2026Q3"
    record.path.write_text(record.render(), encoding="utf-8")
    assert tracking.resolve(project, "candidate", goal_id="vo-backend").experiment_name == (
        "natix-vo-2026Q3"
    )


def test_an_experiment_may_narrow_the_mirror_off(project):
    set_config(project, "tracking:\n  backend: table+mlflow\n")
    (project.experiments_dir / "candidate" / "launch.yml").write_text(
        "tracking:\n  backend: table\nentrypoints: {}\n", encoding="utf-8"
    )
    resolved = tracking.resolve(project, "candidate", goal_id="vo-backend")
    assert not resolved.mirrors
    assert "candidate/launch.yml" in resolved.narrowed_by
    # its sibling is unaffected
    assert tracking.resolve(project, "baseline", goal_id="vo-backend").mirrors


def test_an_experiment_may_never_widen_it(project):
    (project.experiments_dir / "candidate" / "launch.yml").write_text(
        "tracking:\n  backend: table+mlflow\nentrypoints: {}\n", encoding="utf-8"
    )
    with pytest.raises(HirakuError, match="never\nwiden it|never widen it"):
        tracking.resolve(project, "candidate", goal_id="vo-backend")


def test_mlflow_alone_is_refused_with_the_reason(project):
    set_config(project, "tracking:\n  backend: mlflow\n")
    with pytest.raises(HirakuError, match="no 'mlflow' alone on purpose"):
        tracking.resolve(project, "candidate", goal_id="vo-backend")


def test_config_md_overrides_the_machine_config(project):
    """`.hiraku.yml` owns paths; research/config.md is the contract a researcher edits."""
    set_config(project, "tracking:\n  backend: table+mlflow\n")
    assert tracking.resolve(project, "candidate", goal_id="vo-backend").mirrors
    assert project.raw["tracking"]["backend"] == "table"   # untouched


# --------------------------------------------------------------------------- what gets sent


def test_provenance_goes_as_params_and_numbers_as_metrics(project, stub):
    set_config(project, "tracking:\n  backend: table+mlflow\n  mlflow:\n    tracking_uri: http://x\n")
    resolved = tracking.resolve(project, "candidate", goal_id="vo-backend")
    row = {
        "run_id": "r-1", "experiment_id": "candidate", "commit": "abc", "run_kind": "commit",
        "reproducible": "true", "seed": 3, "lockfile_sha256": "uv.lock:deadbeef",
        "ate_rmse_m": 2.15, "completion_rate": 0.91, "notes": "not a number",
    }
    extra, warning = tracking.log_run(project, resolved, row)

    assert warning == ""
    assert extra == {"mlflow_run_id": "mlf-abc123"}
    assert stub.uri == "http://x"
    assert stub.experiment == "vo-backend"
    assert stub.run_name == "r-1"
    # provenance is a fact about the run, never a measurement
    assert stub.params["commit"] == "abc" and stub.params["seed"] == "3"
    assert "seed" not in stub.metrics
    assert stub.metrics == {"ate_rmse_m": 2.15, "completion_rate": 0.91}
    assert stub.params["notes"] == "not a number"
    assert stub.tags["hiraku.run_id"] == "r-1"
    assert stub.tags["hiraku.goal"] == "vo-backend"


def test_nothing_is_sent_when_the_mirror_is_off(project, stub):
    resolved = tracking.resolve(project, "candidate", goal_id="vo-backend")
    assert tracking.log_run(project, resolved, {"run_id": "r-1"}) == ({}, "")
    assert stub.params == {} and stub.tags == {}


# --------------------------------------------------------------------------- when it breaks


def test_a_broken_server_is_a_warning_not_an_exception(project, monkeypatch):
    """The row is the record; the mirror is a convenience. Losing the row would be the real bug."""
    monkeypatch.setitem(sys.modules, "mlflow", StubMlflow(fail=ConnectionError))
    set_config(project, "tracking:\n  backend: table+mlflow\n")
    resolved = tracking.resolve(project, "candidate", goal_id="vo-backend")
    extra, warning = tracking.log_run(project, resolved, {"run_id": "r-1"})
    assert extra == {}
    assert "ConnectionError" in warning and "row was still written" in warning


def test_a_missing_mlflow_says_how_to_install_it(project, monkeypatch):
    monkeypatch.setitem(sys.modules, "mlflow", None)   # import mlflow -> ImportError
    set_config(project, "tracking:\n  backend: table+mlflow\n")
    resolved = tracking.resolve(project, "candidate", goal_id="vo-backend")
    _, warning = tracking.log_run(project, resolved, {"run_id": "r-1"})
    assert "not importable" in warning


def test_check_reports_a_missing_uri_before_a_long_run(project, stub):
    set_config(project, "tracking:\n  backend: table+mlflow\n")
    resolved = tracking.resolve(project, "candidate", goal_id="vo-backend")
    assert "no tracking_uri" in tracking.check(project, resolved)

    set_config(project, "tracking:\n  backend: table+mlflow\n  mlflow:\n    tracking_uri: http://x\n")
    resolved = tracking.resolve(project, "candidate", goal_id="vo-backend")
    assert tracking.check(project, resolved) == ""


def test_check_is_silent_when_there_is_no_mirror(project):
    assert tracking.check(project, tracking.resolve(project, "candidate")) == ""


# --------------------------------------------------------------------------- end to end


def test_the_launcher_records_the_mirror_id_in_the_row(entrypoint, stub):
    from hiraku_auto.launcher import launch

    set_config(entrypoint, "tracking:\n  backend: table+mlflow\n  mlflow:\n    tracking_uri: http://x\n")
    result = launch(entrypoint, "candidate", "train", seed="1")

    assert result.row["mlflow_run_id"] == "mlf-abc123"
    table = read_runs(entrypoint.table_path)
    assert table.rows[-1]["mlflow_run_id"] == "mlf-abc123"
    assert "mlflow_run_id" in PROVENANCE_COLUMNS
    # the metric the entrypoint reported reached MLflow as a metric
    assert stub.metrics["ate_rmse_m"] == pytest.approx(2.001)
    assert stub.tags["hiraku.experiment_id"] == "candidate"


def test_a_broken_mirror_still_records_the_run(entrypoint, monkeypatch, capsys):
    from hiraku_auto.launcher import launch

    monkeypatch.setitem(sys.modules, "mlflow", StubMlflow(fail=ConnectionError))
    set_config(entrypoint, "tracking:\n  backend: table+mlflow\n")
    result = launch(entrypoint, "candidate", "train", seed="1")

    assert result.exit_code == 0
    rows = read_runs(entrypoint.table_path).rows
    assert rows[-1]["run_id"] == result.run_id
    assert rows[-1].get("mlflow_run_id", "") == ""
    assert "row was still written" in capsys.readouterr().err


def test_the_config_template_ships_the_block(bare_project):
    text = Path(bare_project.config_md).read_text(encoding="utf-8")
    assert "backend: table            # table | table+mlflow" in text
    assert "## Tracking" in text
    front = read_record(bare_project.config_md).front
    assert front["tracking"]["backend"] == "table"
