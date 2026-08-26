from __future__ import annotations

import csv
import json
from pathlib import Path

import yaml
from conftest import hiraku, run


def test_init_is_idempotent(repository: Path) -> None:
    result = hiraku(repository, "init")
    assert "already initialized" in result.stdout
    assert (repository / ".agents/skills/hiraku/SKILL.md").is_file()
    assert (repository / ".hiraku/templates/auto/program.md").is_file()


def test_task_and_experiment_lifecycle(repository: Path) -> None:
    created = hiraku(repository, "task", "create", "baseline")
    assert "does not" not in created.stderr
    assert "git switch hiraku/task/baseline" in created.stdout
    assert run(repository, "git", "branch", "--show-current").stdout.strip() == "main"
    assert "hiraku/task/baseline" in run(repository, "git", "branch", "--list").stdout

    shown = hiraku(repository, "task", "show", "baseline", "--json")
    task = json.loads(shown.stdout)
    assert task["id"] == "baseline"
    assert task["branch"] == "hiraku/task/baseline"

    hiraku(
        repository,
        "experiment",
        "create",
        "baseline:regularized",
        "--summary",
        "Test regularization",
        "--entrypoint",
        "train",
        "--entrypoint",
        "train --reg 0.1",
    )
    path = repository / "tasks/baseline/experiments/regularized.yml"
    record = yaml.safe_load(path.read_text())
    assert record["entrypoints"] == ["train", "train --reg 0.1"]
    assert record["status"] == ""
    assert record["created_at"].endswith("Z")
    assert record["updated_at"] == record["created_at"]

    hiraku(
        repository,
        "experiment",
        "set",
        "baseline:regularized",
        "--hypothesis",
        "Regularization improves validation score",
        "--status",
        "active",
    )
    shown = hiraku(repository, "experiment", "show", "baseline:regularized", "--json")
    experiment = json.loads(shown.stdout)
    assert experiment["hypothesis"].startswith("Regularization")
    assert experiment["updated_at"] >= experiment["created_at"]
    assert experiment["run_count"] == 0

    listed = hiraku(repository, "experiment", "list", "baseline", "--json")
    assert [value["id"] for value in json.loads(listed.stdout)] == ["regularized"]
    alias_list = hiraku(repository, "exp", "list", "baseline")
    assert "hypothesis" in alias_list.stdout
    assert "Regularization improves" in alias_list.stdout

    hiraku(repository, "experiment", "delete", "baseline:regularized")
    assert not path.exists()


def test_task_creation_preserves_dirty_worktree_and_does_not_switch(repository: Path) -> None:
    marker = repository / "uncommitted.txt"
    marker.write_text("user work\n", encoding="utf-8")
    hiraku(repository, "task", "create", "dirty-safe")
    assert run(repository, "git", "branch", "--show-current").stdout.strip() == "main"
    assert marker.read_text(encoding="utf-8") == "user work\n"
    assert "uncommitted.txt" in run(repository, "git", "status", "--short").stdout


def test_task_shortcut_is_compact_and_verbose_is_opt_in(repository: Path) -> None:
    hiraku(repository, "task", "create", "baseline")
    run(repository, "git", "switch", "hiraku/task/baseline")
    compact = json.loads(hiraku(repository, "task", "--json").stdout)
    assert compact["id"] == "baseline"
    assert "body" not in compact
    verbose = json.loads(
        hiraku(repository, "task", "show", "baseline", "--verbose", "--json").stdout
    )
    assert "## Goal" in verbose["body"]


def test_markdown_experiment_preserves_body(repository: Path) -> None:
    hiraku(repository, "task", "create", "baseline")
    hiraku(
        repository,
        "experiment",
        "create",
        "baseline:narrative",
        "--format",
        "markdown",
    )
    path = repository / "tasks/baseline/experiments/narrative.md"
    path.write_text(path.read_text(encoding="utf-8") + "\n## Notes\n\nKeep this body.\n")
    hiraku(
        repository,
        "experiment",
        "set",
        "baseline:narrative",
        "--summary",
        "Narrative experiment",
    )
    assert "## Notes\n\nKeep this body." in path.read_text(encoding="utf-8")


def test_rejects_duplicate_extensions(repository: Path) -> None:
    hiraku(repository, "task", "create", "baseline")
    hiraku(repository, "experiment", "create", "baseline:trial")
    duplicate = repository / "tasks/baseline/experiments/trial.md"
    duplicate.write_text(
        "---\n"
        "id: trial\n"
        "task_id: baseline\n"
        "entrypoints: []\n"
        "created_at: '2026-01-01T00:00:00Z'\n"
        "updated_at: '2026-01-01T00:00:00Z'\n"
        "---\n",
        encoding="utf-8",
    )
    result = hiraku(repository, "experiment", "list", "baseline", check=False)
    assert result.returncode == 2
    assert "exists as both" in result.stderr


def test_launch_and_rank_runs_from_bound_commit(repository: Path) -> None:
    hiraku(repository, "task", "create", "baseline")
    run(repository, "git", "switch", "hiraku/task/baseline")
    script = repository / "train.py"
    script.write_text(
        """\
import argparse
import csv
import os
from datetime import UTC, datetime
from pathlib import Path

parser = argparse.ArgumentParser()
parser.add_argument("--seed")
parser.add_argument("--reg")
args = parser.parse_args()
path = Path(os.environ["HIRAKU_TABLE_PATH"])
write_header = not path.exists()
with path.open("a", encoding="utf-8", newline="") as stream:
    fields = ["run_id", "task_id", "experiment_id", "commit", "created_at", "score"]
    writer = csv.DictWriter(stream, fieldnames=fields)
    if write_header:
        writer.writeheader()
    writer.writerow({
        "run_id": f"run-{args.reg or 'default'}",
        "task_id": os.environ["HIRAKU_TASK_ID"],
        "experiment_id": os.environ["HIRAKU_EXPERIMENT_ID"],
        "commit": os.environ["HIRAKU_SOURCE_COMMIT"],
        "created_at": datetime.now(UTC).isoformat().replace("+00:00", "Z"),
        "score": "0.9" if args.reg == "0.1" else "0.5",
    })
""",
        encoding="utf-8",
    )
    launch = {
        "entrypoints": {
            "train": {
                "command": ["python", "train.py"],
                "args": {"seed": 1},
                "cwd": ".",
            }
        }
    }
    (repository / "tasks/baseline/launch.yml").write_text(
        yaml.safe_dump(launch, sort_keys=False), encoding="utf-8"
    )
    entrypoints = json.loads(hiraku(repository, "task", "entrypoints", "baseline", "--json").stdout)
    assert entrypoints[0]["name"] == "train"
    assert entrypoints[0]["args"] == {"seed": 1}
    hiraku(
        repository,
        "experiment",
        "create",
        "baseline:regularized",
        "--entrypoint",
        "train --reg 0.1",
    )
    run(repository, "git", "add", ".")
    run(repository, "git", "commit", "-m", "implement regularized experiment")
    commit = run(repository, "git", "rev-parse", "HEAD").stdout.strip()
    hiraku(
        repository,
        "experiment",
        "set",
        "baseline:regularized",
        "--commit",
        "HEAD",
    )

    launched = hiraku(
        repository,
        "launch",
        "--experiment",
        "regularized",
        "baseline:train",
        "--reg",
        "0.1",
    )
    assert launched.returncode == 0
    with (repository / "tasks/baseline/runs.csv").open(newline="") as stream:
        rows = list(csv.DictReader(stream))
    assert rows[0]["commit"] == commit
    assert rows[0]["experiment_id"] == "regularized"
    assert rows[0]["created_at"].endswith("Z")

    ranked = json.loads(
        hiraku(
            repository,
            "runs",
            "baseline",
            "--sort",
            "score",
            "--descending",
            "--json",
        ).stdout
    )
    assert ranked[0]["_rank"] == 1
    assert ranked[0]["score"] == "0.9"
    assert ranked[0]["_commit_mismatch"] is False

    shown = json.loads(
        hiraku(repository, "experiment", "show", "baseline:regularized", "--json").stdout
    )
    assert shown["run_count"] == 1
    assert shown["commit_mismatches"] == []

    table_path = repository / "tasks/baseline/runs.csv"
    table = table_path.read_text(encoding="utf-8").replace(commit, "0" * 40)
    table_path.write_text(table, encoding="utf-8")
    mismatched = json.loads(hiraku(repository, "runs", "baseline", "--json").stdout)
    assert mismatched[0]["_commit_mismatch"] is True


def test_experiment_launch_must_match_declared_invocation(repository: Path) -> None:
    hiraku(repository, "task", "create", "baseline")
    hiraku(
        repository,
        "experiment",
        "create",
        "baseline:trial",
        "--entrypoint",
        "train --reg 0.1",
    )
    result = hiraku(
        repository,
        "launch",
        "--experiment",
        "trial",
        "baseline:train",
        "--reg",
        "0.2",
        check=False,
    )
    assert result.returncode == 2
    assert "not declared" in result.stderr


def test_working_tree_launch_propagates_child_exit_status(repository: Path) -> None:
    hiraku(repository, "task", "create", "failure")
    (repository / "fail.py").write_text("raise SystemExit(7)\n", encoding="utf-8")
    launch = {"entrypoints": {"fail": {"command": ["python", "fail.py"], "cwd": "."}}}
    (repository / "tasks/failure/launch.yml").write_text(
        yaml.safe_dump(launch, sort_keys=False), encoding="utf-8"
    )
    result = hiraku(repository, "launch", "failure:fail", check=False)
    assert result.returncode == 7
