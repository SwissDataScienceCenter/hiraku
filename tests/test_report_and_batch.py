"""The report, and launching several experiments at once."""

from __future__ import annotations

import json

import pytest

from hiraku_auto import gitops
from hiraku_auto import report as report_mod
from hiraku_auto.cli import _devices, _launchable, launch_batch, main
from hiraku_auto.errors import HirakuError
from hiraku_auto.records import load_experiment, save_experiment
from hiraku_auto.scaffold import create_experiment
from hiraku_auto.tree import build

# --------------------------------------------------------------------------- report


def test_report_quotes_the_records_rather_than_inventing_prose(entrypoint):
    record = load_experiment(entrypoint, "candidate")
    record.front.update({"status": "concluded", "verdict": "supports"})
    record.set_section("Conclusions", "A SENTENCE ONLY THE RECORD CONTAINS.")
    save_experiment(record)

    text = report_mod.render(entrypoint, "vo-backend")
    assert "A SENTENCE ONLY THE RECORD CONTAINS." in text
    assert "## What is known" in text
    assert "`candidate`" in text


def test_report_separates_known_ruled_out_and_open(entrypoint):
    for node, status, verdict in (
        ("baseline", "concluded", "supports"),
        ("candidate", "concluded", "refutes"),
    ):
        record = load_experiment(entrypoint, node)
        record.front.update({"status": status, "verdict": verdict})
        record.set_section("Conclusions", f"conclusion for {node}")
        save_experiment(record)
    create_experiment(entrypoint, "still-open", parent="candidate", status="approved",
                      expectation="an open question")

    text = report_mod.render(entrypoint, "vo-backend")
    known = text.split("## What is known")[1].split("## What was ruled out")[0]
    ruled = text.split("## What was ruled out")[1].split("## What is still open")[0]
    still = text.split("## What is still open")[1]
    assert "baseline" in known and "candidate" not in known
    assert "candidate" in ruled
    assert "still-open" in still


def test_report_will_not_call_a_promotion_candidate_a_result(entrypoint):
    from hiraku_auto.launcher import launch

    launch(entrypoint, "candidate", "train", commit="HEAD", seed=1)
    text = report_mod.render(entrypoint, "vo-backend")
    assert "Nothing has been promoted yet" in text
    assert "a candidate, not a result" in text


def test_report_says_when_no_run_is_eligible(entrypoint):
    from hiraku_auto.launcher import launch

    launch(entrypoint, "candidate", "train", seed=1)  # working tree: not reproducible
    text = report_mod.render(entrypoint, "vo-backend")
    assert "no run is eligible" in text


def test_report_surfaces_gaps_in_the_record(entrypoint):
    record = load_experiment(entrypoint, "candidate")
    record.front["status"] = "concluded"  # no verdict
    save_experiment(record)
    text = report_mod.render(entrypoint, "vo-backend")
    assert "## Gaps in the record" in text
    assert "concluded without a verdict" in text


def test_report_rejects_an_unknown_goal(project):
    with pytest.raises(HirakuError, match="does not exist"):
        report_mod.render(project, "no-such-goal")


def test_report_command_writes_a_file(entrypoint, tmp_path, capsys):
    out = tmp_path / "r.md"
    assert main(["--project", str(entrypoint.root), "report", "vo-backend", "-o", str(out)]) == 0
    assert out.read_text(encoding="utf-8").startswith("# vo-backend — report")


# --------------------------------------------------------------------------- batch launch


def test_devices_expands_a_count_and_a_list():
    assert _devices("8", 8) == [str(i) for i in range(8)]
    assert _devices("0,1,4,5", 4) == ["0", "1", "4", "5"]
    # More slots than devices wraps, so a slot always has one.
    assert _devices("0,1", 4) == ["0", "1", "0", "1"]
    # No spec means inherit whatever the caller had.
    assert _devices(None, 3) == ["", "", ""]


def test_launchable_is_approved_and_never_run(entrypoint):
    forest = build(entrypoint)
    assert _launchable(forest, "vo-backend") == ["baseline", "candidate"]

    from hiraku_auto.launcher import launch

    launch(entrypoint, "candidate", "train", commit="HEAD", seed=1)
    assert _launchable(build(entrypoint), "vo-backend") == ["baseline"]


def test_batch_launches_every_experiment_and_pins_a_device(entrypoint):
    """Six-way parallelism is the point; each run must get its own device and its own row."""
    exp_dir = entrypoint.experiments_dir / "candidate"
    (exp_dir / "train.py").write_text(
        "import json, os\n"
        "json.dump({'ate_rmse_m': 1.0, 'device': os.environ.get('CUDA_VISIBLE_DEVICES', '')},\n"
        "          open(os.environ['HIRAKU_METRICS_PATH'], 'w'))\n",
        encoding="utf-8",
    )
    for name in ("a", "b", "c"):
        create_experiment(entrypoint, f"batch-{name}", goal="vo-backend", status="approved",
                          expectation="x")
        target = entrypoint.experiments_dir / f"batch-{name}"
        (target / "launch.yml").write_text(
            'entrypoints:\n  train:\n    command: ["python3", "../candidate/train.py"]\n'
            "    cwd: .\n",
            encoding="utf-8",
        )
    gitops.run_git(entrypoint.root, "add", "-A")
    gitops.run_git(entrypoint.root, "commit", "-qm", "batch entrypoints")

    ids = ["batch-a", "batch-b", "batch-c"]
    results = launch_batch(entrypoint, ids, "train", max_parallel=3, gpus="0,1,2", commit="HEAD")

    assert {e for e, _ in results} == set(ids)
    assert all(r is not None and r.exit_code == 0 for _, r in results)
    assert {r.row["device"] for _, r in results} == {"0", "1", "2"}
    # One shared table, one row each, none lost to a concurrent append.
    from hiraku_auto.runs import read_runs

    rows = read_runs(entrypoint.table_path).rows
    assert len({r["run_id"] for r in rows if r["experiment_id"] in ids}) == 3


def test_batch_defaults_each_run_to_its_own_branch(entrypoint, monkeypatch):
    """Parallel siblings hold different code, so each must run from its own branch."""
    seen: dict[str, str] = {}
    import hiraku_auto.cli as cli_mod

    def fake_launch(project, experiment_id, entrypoint_name, **kwargs):
        seen[experiment_id] = kwargs["commit"]

        class _R:
            run_id, exit_code, row, reproducible = "r", 0, {}, True

        return _R()

    monkeypatch.setattr(cli_mod, "do_launch", fake_launch)

    # No branch yet: fall back to HEAD rather than failing.
    launch_batch(entrypoint, ["candidate"], "train", max_parallel=1)
    assert seen["candidate"] == "HEAD"

    # Once the branch exists, that is what the run should execute.
    gitops.create_branch(entrypoint.root, "exp/candidate")
    launch_batch(entrypoint, ["candidate"], "train", max_parallel=1)
    assert seen["candidate"] == "exp/candidate"


def test_loop_reports_a_failed_batch_member(entrypoint, capsys):
    exp_dir = entrypoint.experiments_dir / "candidate"
    (exp_dir / "launch.yml").write_text(
        'entrypoints:\n  train:\n    command: ["python3", "-c", "import sys; sys.exit(3)"]\n'
        "    cwd: .\n",
        encoding="utf-8",
    )
    gitops.run_git(entrypoint.root, "add", "-A")
    gitops.run_git(entrypoint.root, "commit", "-qm", "failing entrypoint")
    code = main(
        ["--project", str(entrypoint.root), "loop", "vo-backend", "--launch", "train",
         "--max-parallel", "2", "--max-iterations", "1"]
    )
    out = capsys.readouterr().out
    assert code == 1 and "run(s) failed" in out


def test_next_json_stays_machine_readable(entrypoint, capsys):
    main(["--project", str(entrypoint.root), "next", "vo-backend", "--json"])
    assert json.loads(capsys.readouterr().out)["action"] == "launch"
