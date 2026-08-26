from __future__ import annotations

import csv
import json
from pathlib import Path

import yaml
from conftest import hiraku, run

from hiraku.config import load_project


def test_init_is_idempotent(repository: Path) -> None:
    result = hiraku(repository, "init")
    assert "already initialized" in result.stdout
    assert (repository / ".agents/skills/hiraku/SKILL.md").is_file()
    assert (repository / ".agents/skills/hiraku/references/research.md").is_file()
    assert (repository / ".agents/skills/hiraku/references/review.md").is_file()
    assert (repository / ".hiraku/templates/auto/program.md").is_file()


def test_skill_routes_workflows_and_preserves_customized_resources(repository: Path) -> None:
    skill_dir = repository / ".agents/skills/hiraku"
    router = (skill_dir / "SKILL.md").read_text(encoding="utf-8")
    research = (skill_dir / "references/research.md").read_text(encoding="utf-8")
    review = (skill_dir / "references/review.md").read_text(encoding="utf-8")
    assert "references/research.md" in router
    assert "references/review.md" in router
    assert "program.md" in router
    assert "hiraku runs <task> --json" in research
    assert "Never edit `runs.csv` or MLflow data" in research
    assert "## Changelog" in review
    assert "Never approve or merge" in review

    customized = skill_dir / "references/research.md"
    customized.write_text(research + "\nProject-specific guidance.\n", encoding="utf-8")
    result = hiraku(repository, "init", check=False)
    assert result.returncode == 2
    assert "Refusing to overwrite customized file" in result.stderr
    assert customized.read_text(encoding="utf-8").endswith("Project-specific guidance.\n")


def test_bundled_programs_define_stage_three_boundaries(repository: Path) -> None:
    guided = (repository / ".hiraku/templates/guided/program.md").read_text(encoding="utf-8")
    autonomous = (repository / ".hiraku/templates/auto/program.md").read_text(encoding="utf-8")
    for checkpoint in (
        "Task definition",
        "Experiment intent",
        "Implementation",
        "Launch",
        "Interpretation",
        "Synthesis and publication",
    ):
        assert checkpoint in guided
    assert "explicit approval at each checkpoint" in guided
    assert "hiraku runs <task> --json" in guided
    assert "Iteration limit and other stopping conditions" in autonomous
    assert "Within those bounds, repeat this loop" in autonomous
    assert "hiraku runs <task> --json" in autonomous
    assert "Never edit tracking data" in autonomous


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
    task_path = repository / "tasks/baseline/task.md"
    task_path.write_text(
        task_path.read_text(encoding="utf-8").replace(
            "metrics: []",
            "metrics:\n  - name: score\n    direction: maximize",
        ),
        encoding="utf-8",
    )
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
    fields = ["run_id", "task_id", "experiment_id", "commit", "created_at", "score", "loss"]
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
        "loss": "0.2",
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

    compact = hiraku(repository, "runs", "baseline").stdout
    header = compact.splitlines()[0]
    assert "rank" in header
    assert "score" in header
    assert "loss" not in header
    assert "task_id" not in header
    assert commit[:10] in compact
    assert commit not in compact

    selected = hiraku(repository, "runs", "baseline", "--metrics", "loss,score").stdout
    selected_header = selected.splitlines()[0]
    assert selected_header.index("loss") < selected_header.index("score")

    missing_direction = hiraku(
        repository,
        "runs",
        "baseline",
        "--sort",
        "loss",
        check=False,
    )
    assert missing_direction.returncode == 2
    assert "has no declared direction" in missing_direction.stderr

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

    human_list = hiraku(repository, "experiment", "list", "baseline").stdout
    human_show = hiraku(repository, "experiment", "show", "baseline:regularized").stdout
    assert commit[:10] in human_list
    assert commit not in human_list
    assert commit[:10] in human_show
    assert commit not in human_show

    table_path = repository / "tasks/baseline/runs.csv"
    table = table_path.read_text(encoding="utf-8").replace(commit, "0" * 40)
    table_path.write_text(table, encoding="utf-8")
    mismatched = json.loads(hiraku(repository, "runs", "baseline", "--json").stdout)
    assert mismatched[0]["_commit_mismatch"] is True


def test_task_metric_declarations_are_validated(repository: Path) -> None:
    hiraku(repository, "task", "create", "metrics")
    task_path = repository / "tasks/metrics/task.md"
    original = task_path.read_text(encoding="utf-8")
    task_path.write_text(
        original.replace(
            "metrics: []",
            "metrics:\n  - name: score\n    direction: maximize",
        ),
        encoding="utf-8",
    )
    task = json.loads(hiraku(repository, "task", "show", "metrics", "--json").stdout)
    assert task["metrics"] == [{"name": "score", "direction": "maximize"}]

    task_path.write_text(
        original.replace(
            "metrics: []",
            "metrics:\n  - name: score\n    direction: sideways",
        ),
        encoding="utf-8",
    )
    invalid = hiraku(repository, "task", "show", "metrics", check=False)
    assert invalid.returncode == 2
    assert "must be minimize or maximize" in invalid.stderr


def test_remote_mlflow_configuration_contract(repository: Path) -> None:
    config_path = repository / ".hiraku.yml"
    config = yaml.safe_load(config_path.read_text(encoding="utf-8"))
    config.update(
        {
            "tracking_backend": "mlflow",
            "mlflow_tracking_uri": "https://mlflow.example.org",
        }
    )
    config_path.write_text(yaml.safe_dump(config, sort_keys=False), encoding="utf-8")
    project = load_project(repository)
    assert project.remote_mlflow is True
    assert project.mlflow_tracking_uri == "https://mlflow.example.org"

    config["mlflow_tracking_db"] = "tracking/mlflow.db"
    config_path.write_text(yaml.safe_dump(config, sort_keys=False), encoding="utf-8")
    conflict = hiraku(repository, "task", "show", "missing", check=False)
    assert conflict.returncode == 2
    assert "mutually exclusive" in conflict.stderr

    config.pop("mlflow_tracking_db")
    config["mlflow_tracking_uri"] = "https://user:secret@mlflow.example.org"
    config_path.write_text(yaml.safe_dump(config, sort_keys=False), encoding="utf-8")
    credentials = hiraku(repository, "task", "show", "missing", check=False)
    assert credentials.returncode == 2
    assert "must not contain credentials" in credentials.stderr
    assert "secret" not in credentials.stderr

    config["mlflow_tracking_uri"] = "https://mlflow.example.org?token=secret-token"
    config_path.write_text(yaml.safe_dump(config, sort_keys=False), encoding="utf-8")
    query_secret = hiraku(repository, "task", "show", "missing", check=False)
    assert query_secret.returncode == 2
    assert "must not contain a query or fragment" in query_secret.stderr
    assert "secret-token" not in query_secret.stderr


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
