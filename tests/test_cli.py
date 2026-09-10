from __future__ import annotations

import json
import shlex
import subprocess
import sys
from importlib.metadata import version as package_version
from pathlib import Path

import pytest
from click.testing import CliRunner

from hiraku.agent import Action, Context, Program
from hiraku.cli import main
from hiraku.config import HirakuConfig
from hiraku.data import Experiment, Task, load_experiment, load_task
from hiraku.git import GitRepository

HELP_PATHS = [
    [],
    ["init"],
    ["status"],
    ["launch"],
    *(
        [group]
        for group in (
            "task",
            "experiment",
            "exp",
            "proposal",
            "program",
            "action",
            "context",
            "run",
        )
    ),
    *(
        [group, command]
        for group, commands in {
            "task": (
                "create",
                "list",
                "show",
                "update",
                "edit",
                "delete",
                "set",
                "entrypoints",
            ),
            "experiment": (
                "create",
                "list",
                "show",
                "update",
                "edit",
                "delete",
                "set",
                "clear",
                "entrypoints",
            ),
            "proposal": ("list",),
            "program": ("create", "list", "show", "update", "edit", "delete"),
            "action": ("create", "list", "show", "update", "edit", "delete"),
            "context": ("create", "list", "show", "update", "edit", "delete"),
            "run": ("list",),
        }.items()
        for command in commands
    ),
]


def _git(path: Path, *arguments: str) -> str:
    result = subprocess.run(
        ["git", *arguments], cwd=path, text=True, capture_output=True, check=True
    )
    return result.stdout.strip()


def _project(path: Path, *, git: bool = False) -> HirakuConfig:
    if git:
        _git(path, "init", "-q")
        _git(path, "config", "user.email", "test@example.invalid")
        _git(path, "config", "user.name", "Test")
        (path / "tracked.txt").write_text("initial", encoding="utf-8")
        _git(path, "add", "tracked.txt")
        _git(path, "commit", "-qm", "initial")
    return HirakuConfig.init(path)


@pytest.mark.parametrize("path", HELP_PATHS)
def test_every_help_path_is_lazy(path: list[str], tmp_path: Path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    result = CliRunner().invoke(main, [*path, "--help"])
    assert result.exit_code == 0, result.output
    assert result.output.startswith("Usage: hiraku")
    assert not (tmp_path / "hiraku.yml").exists()


def test_help_is_lazy_and_init_and_status_work(tmp_path: Path, monkeypatch) -> None:
    runner = CliRunner()
    result = runner.invoke(main, ["--help"])
    assert result.exit_code == 0
    assert "Manage Hiraku projects" in result.output
    assert not (tmp_path / "hiraku.yml").exists()

    monkeypatch.chdir(tmp_path)
    _git(tmp_path, "init", "-q")
    result = runner.invoke(main, ["init"])
    assert result.exit_code == 0
    marker = tmp_path / "hiraku.yml"
    assert str(marker) in result.output
    result = runner.invoke(main, ["status", "--json"])
    assert result.exit_code == 0
    status = json.loads(result.output)
    assert status["active_task_id"] is None
    assert status["git_branch"] is not None


def test_version_reports_package_metadata() -> None:
    result = CliRunner().invoke(main, ["--version"])

    assert result.exit_code == 0, result.output
    assert result.output.strip() == f"v{package_version('hiraku')}"


def test_init_accepts_tracking_backend(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    _git(tmp_path, "init", "-q")

    result = CliRunner().invoke(main, ["init", "--tracking-backend", "mlflow"])

    assert result.exit_code == 0, result.output
    assert HirakuConfig.discover(tmp_path).tracking_backend == {"type": "mlflow"}


def test_bare_init_requires_current_directory_to_be_git_root(
    tmp_path: Path, monkeypatch
) -> None:
    runner = CliRunner()
    monkeypatch.chdir(tmp_path)

    outside = runner.invoke(main, ["init"])
    assert outside.exit_code != 0
    assert "must be initialized at a Git repository root" in outside.output
    assert not (tmp_path / "hiraku.yml").exists()

    _git(tmp_path, "init", "-q")
    nested = tmp_path / "nested"
    nested.mkdir()
    monkeypatch.chdir(nested)
    result = runner.invoke(main, ["init"])
    assert result.exit_code != 0
    assert str(tmp_path) in result.output
    assert not (nested / "hiraku.yml").exists()


def test_ignore_git_bypasses_git_root_requirement(
    tmp_path: Path, monkeypatch
) -> None:
    runner = CliRunner()
    repository = tmp_path / "repository"
    repository.mkdir()
    _git(repository, "init", "-q")
    nested = repository / "nested"

    result = runner.invoke(main, ["init", str(nested)])

    assert result.exit_code != 0
    assert not (nested / "hiraku.yml").exists()

    result = runner.invoke(main, ["init", str(nested), "--ignore-git"])

    assert result.exit_code == 0, result.output
    assert (nested / "hiraku.yml").is_file()

    outside = tmp_path / "outside"
    result = runner.invoke(main, ["init", str(outside), "--ignore-git"])
    assert result.exit_code == 0, result.output
    assert (outside / "hiraku.yml").is_file()


def test_task_crud_branches_and_active_deletion(tmp_path: Path, monkeypatch) -> None:
    config = _project(tmp_path, git=True)
    monkeypatch.chdir(tmp_path)
    runner = CliRunner()

    result = runner.invoke(
        main,
        ["task", "create", "demo", "--goal", "Ship", "--set", "priority=2"],
    )
    assert result.exit_code == 0, result.output
    task = load_task("demo", config=config)
    assert task.branch == "hiraku/tasks/demo"
    assert task.goal == "Ship"
    assert task.model_extra["priority"] == 2
    assert GitRepository(config).branch_exists(task.branch)
    assert HirakuConfig.discover(tmp_path).active_task_id == "demo"

    result = runner.invoke(
        main,
        ["task", "update", "demo", "--summary", "Ready", "--unset", "priority"],
    )
    assert result.exit_code == 0, result.output
    shown = runner.invoke(main, ["task", "show", "demo", "--json"])
    assert json.loads(shown.output)["summary"] == "Ready"
    assert "priority" not in json.loads(shown.output)

    result = runner.invoke(main, ["task", "delete", "demo"])
    assert result.exit_code == 0, result.output
    refreshed = HirakuConfig.discover(tmp_path)
    assert refreshed.active_task_id is None
    assert refreshed.active_experiment_id is None


def test_edit_opens_malformed_task_for_recovery(tmp_path: Path, monkeypatch) -> None:
    config = _project(tmp_path)
    task = Task.create(
        "demo",
        config=config,
        entrypoints={"train": {"command": ["uv"], "args": {"test": True}}},
    )
    malformed = task.path.read_text(encoding="utf-8").replace(
        "args:\n      test: true", "args: ['--test']"
    )
    task.path.write_text(malformed, encoding="utf-8")
    editor = tmp_path / "repair.py"
    editor.write_text(
        "from pathlib import Path\n"
        "import sys\n"
        "path = Path(sys.argv[1])\n"
        "path.write_text(path.read_text().replace(\"args: ['--test']\", "
        '"args: {}"))\n',
        encoding="utf-8",
    )
    monkeypatch.setenv("EDITOR", shlex.join([sys.executable, str(editor)]))
    monkeypatch.chdir(tmp_path)
    runner = CliRunner()

    shown = runner.invoke(main, ["task", "show"])
    assert shown.exit_code != 0
    assert "Entrypoint args must be a mapping" in shown.output

    edited = runner.invoke(main, ["task", "edit"])
    assert edited.exit_code == 0, edited.output
    assert "Warning: Task 'demo' is currently invalid" in edited.stderr
    assert "Opening" in edited.stderr
    assert "Validated task demo" in edited.stdout
    assert load_task("demo", config=config).entrypoints["train"].args == {}


def test_edit_retains_malformed_file_when_recovery_is_incomplete(
    tmp_path: Path, monkeypatch
) -> None:
    config = _project(tmp_path)
    task = Task.create("demo", config=config)
    malformed = task.path.read_text(encoding="utf-8").replace("id: demo", "id: wrong")
    task.path.write_text(malformed, encoding="utf-8")
    editor = tmp_path / "no_op.py"
    editor.write_text("# Leave the edited file unchanged.\n", encoding="utf-8")
    monkeypatch.setenv("EDITOR", shlex.join([sys.executable, str(editor)]))
    monkeypatch.chdir(tmp_path)

    result = CliRunner().invoke(main, ["task", "edit", "demo"])
    assert result.exit_code != 0
    assert "Warning: Task 'demo' is currently invalid" in result.stderr
    assert "does not match registry path" in result.stderr
    assert task.path.read_text(encoding="utf-8") == malformed


def test_experiment_optional_branch_proposals_and_alias(
    tmp_path: Path, monkeypatch
) -> None:
    config = _project(tmp_path, git=True)
    task = Task.create("demo", branch="hiraku/tasks/demo", activate=True, config=config)
    GitRepository(config).create_task_branch(task.id)
    monkeypatch.chdir(tmp_path)
    runner = CliRunner()

    result = runner.invoke(main, ["experiment", "create", "--branch", "baseline"])
    assert result.exit_code == 0, result.output
    baseline = load_experiment("baseline", task=task, config=config)
    assert baseline.branch == "hiraku/experiments/demo/baseline"

    result = runner.invoke(
        main, ["exp", "create", "--branch", "shared", "second", "--no-set"]
    )
    assert result.exit_code == 0, result.output
    second = load_experiment("second", task=task, config=config)
    assert second.branch == "shared"

    proposals = runner.invoke(main, ["proposal", "list", "--json"])
    assert proposals.exit_code == 0, proposals.output
    assert [item["id"] for item in json.loads(proposals.output)] == [
        "baseline",
        "second",
    ]


def test_experiment_commit_refs_resolve_in_cli_before_persistence(
    tmp_path: Path, monkeypatch
) -> None:
    config = _project(tmp_path, git=True)
    task = Task.create("demo", branch="main", config=config)
    expected = GitRepository(config).head
    monkeypatch.chdir(tmp_path)
    runner = CliRunner()

    created = runner.invoke(
        main,
        ["experiment", "create", "bound", "--commit", "HEAD", "--no-set"],
    )
    assert created.exit_code == 0, created.output
    record = load_experiment("bound", task=task, config=config)
    assert record.commit == expected
    listed = runner.invoke(main, ["experiment", "list", "--task", "demo"])
    assert listed.exit_code == 0, listed.output
    assert expected[:8] in listed.output
    assert expected not in listed.output
    listed_json = runner.invoke(
        main, ["experiment", "list", "--task", "demo", "--json"]
    )
    assert listed_json.exit_code == 0, listed_json.output
    assert json.loads(listed_json.output)[0]["commit"] == expected

    proposal = Experiment.create("proposal", task=task, config=config)
    updated = runner.invoke(
        main,
        ["experiment", "update", "proposal", "--task", "demo", "--commit", "HEAD"],
    )
    assert updated.exit_code == 0, updated.output
    assert load_experiment("proposal", task=task, config=config).commit == expected

    rejected = runner.invoke(
        main,
        [
            "experiment",
            "create",
            "bypass",
            "--task",
            "demo",
            "--set",
            "commit=HEAD",
        ],
    )
    assert rejected.exit_code != 0
    assert "Use --commit REF" in rejected.output

    unresolved = runner.invoke(
        main,
        [
            "experiment",
            "create",
            "unresolved",
            "--task",
            "demo",
            "--commit",
            "missing-ref",
        ],
    )
    assert unresolved.exit_code != 0
    assert not (config.tasks_dir / "demo" / "experiments" / "unresolved.md").exists()

    unbound = runner.invoke(
        main,
        ["experiment", "update", "proposal", "--task", "demo", "--unset", "commit"],
    )
    assert unbound.exit_code == 0, unbound.output
    assert proposal.path is not None
    assert load_experiment("proposal", task=task, config=config).commit is None


def test_entrypoints_render_commands_and_preserve_json(
    tmp_path: Path, monkeypatch
) -> None:
    config = _project(tmp_path)
    task_entrypoint = {
        "command": ["python", "train model.py"],
        "args": {
            "learning_rate": 0.1,
            "tag": ["a", "two words"],
            "verbose": True,
        },
    }
    task = Task.create("demo", config=config, entrypoints={"train": task_entrypoint})
    experiment_entrypoint = {
        "command": ["python", "evaluate.py"],
        "args": {"checkpoint": "best model.pt"},
    }
    Experiment.create(
        "trial",
        task=task,
        config=config,
        entrypoints={"evaluate": experiment_entrypoint},
    )
    monkeypatch.chdir(tmp_path)
    runner = CliRunner()

    task_result = runner.invoke(main, ["task", "entrypoints", "demo"])
    assert task_result.exit_code == 0, task_result.output
    assert "name   command" in task_result.output
    assert (
        "python 'train model.py' --learning-rate 0.1 --tag a "
        "--tag 'two words' --verbose" in task_result.output
    )

    experiment_result = runner.invoke(main, ["experiment", "entrypoints"])
    assert experiment_result.exit_code == 0, experiment_result.output
    assert "python evaluate.py --checkpoint 'best model.pt'" in experiment_result.output

    json_result = runner.invoke(main, ["task", "entrypoints", "demo", "--json"])
    assert json_result.exit_code == 0, json_result.output
    assert json.loads(json_result.output) == {"train": task_entrypoint}


def test_agent_record_commands_and_mutation_conflicts(
    tmp_path: Path, monkeypatch
) -> None:
    config = _project(tmp_path)
    Task.create("demo", goal="original", config=config)
    monkeypatch.chdir(tmp_path)
    runner = CliRunner()

    result = runner.invoke(
        main, ["context", "create", "literature", "--set", "reviewed=true"]
    )
    assert result.exit_code == 0, result.output
    shown = runner.invoke(main, ["context", "show", "literature", "--json"])
    assert json.loads(shown.output)["reviewed"] is True

    conflict = runner.invoke(
        main,
        ["task", "update", "demo", "--goal", "one", "--set", "goal=two"],
    )
    assert conflict.exit_code != 0
    assert "supplied more than once" in conflict.output
    assert load_task("demo", config=config).goal == "original"

    action = runner.invoke(main, ["action", "create", "experiment/review"])
    assert action.exit_code == 0, action.output
    listing = runner.invoke(main, ["action", "list", "--entity", "experiment"])
    assert "review" in listing.output


def test_show_one_front_matter_field_from_every_record_type(
    tmp_path: Path, monkeypatch
) -> None:
    config = _project(tmp_path)
    task = Task.create("demo", config=config, priority=2)
    Experiment.create("baseline", task=task, config=config, score=0.9)
    Program(
        id="budgeted",
        time_budget=300,
        enabled=True,
        optional=None,
        tags=["fast", "repeatable"],
        resources={"gpu": 1},
    ).save(config.programs_dir / "budgeted/program.md")
    Context(id="literature", reviewed=True).save(
        config.contexts_dir / "literature/context.md"
    )
    Action(id="review", entity="experiment", status="ready").save(
        config.actions_dir / "experiment/review/action.md"
    )
    monkeypatch.chdir(tmp_path)
    runner = CliRunner()

    cases = [
        (["task", "show", "demo", "--field", "priority"], "2\n"),
        (
            [
                "experiment",
                "show",
                "baseline",
                "--task",
                "demo",
                "--field",
                "score",
            ],
            "0.9\n",
        ),
        (
            ["program", "show", "budgeted", "--field", "time_budget"],
            "300\n",
        ),
        (["context", "show", "literature", "--field", "reviewed"], "true\n"),
        (
            ["action", "show", "experiment/review", "--field", "status"],
            "ready\n",
        ),
    ]
    for arguments, expected in cases:
        result = runner.invoke(main, arguments)
        assert result.exit_code == 0, result.output
        assert result.output == expected

    assert runner.invoke(
        main, ["program", "show", "budgeted", "--field", "optional"]
    ).output == "\n"
    assert runner.invoke(
        main, ["program", "show", "budgeted", "--field", "tags"]
    ).output == '["fast","repeatable"]\n'
    assert runner.invoke(
        main, ["program", "show", "budgeted", "--field", "resources"]
    ).output == '{"gpu":1}\n'

    json_result = runner.invoke(
        main,
        ["program", "show", "budgeted", "--field", "time_budget", "--json"],
    )
    assert json_result.exit_code == 0, json_result.output
    assert json.loads(json_result.output) == 300

    missing = runner.invoke(
        main, ["program", "show", "budgeted", "--field", "missing"]
    )
    assert missing.exit_code != 0
    assert "Field 'missing' not found in Program 'budgeted'" in missing.output

    body = runner.invoke(
        main, ["program", "show", "budgeted", "--field", "markdown"]
    )
    assert body.exit_code != 0
    assert "Field 'markdown' not found" in body.output

    full_record = runner.invoke(main, ["program", "show", "budgeted"])
    assert full_record.exit_code == 0, full_record.output
    assert full_record.output.startswith("---\n")


def test_launch_boundary_output_and_exit_status(
    tmp_path: Path, monkeypatch, capfd
) -> None:
    config = _project(tmp_path)
    Task.create(
        "demo",
        config=config,
        entrypoints={
            "show": {
                "command": [
                    "/bin/sh",
                    "-c",
                    'printf "%s|%s|%s" "$1" "$2" "$3"',
                    "shell",
                ],
                "args": {"seed": 1},
            },
            "fail": {"command": ["/bin/sh", "-c", "exit 7"]},
        },
    )
    monkeypatch.chdir(tmp_path)
    runner = CliRunner()

    result = runner.invoke(
        main, ["launch", "show", "--dispatch", "launch", "--", "--seed", "3"]
    )
    assert result.exit_code == 0, result.output
    assert capfd.readouterr().out == "--seed|3|"
    unknown = runner.invoke(main, ["launch", "show", "--", "--lr", "2"])
    assert unknown.exit_code == 0
    assert "Warning: argument --lr is not declared" in unknown.output
    failed = runner.invoke(main, ["launch", "fail"])
    assert failed.exit_code == 7


def test_launch_dry_run_resolves_commands_without_execution(
    tmp_path: Path, monkeypatch
) -> None:
    config = _project(tmp_path)
    marker = tmp_path / "executed"
    task = Task.create(
        "demo",
        config=config,
        entrypoints={
            "show": {
                "command": ["/bin/sh", "-c", f"touch {shlex.quote(str(marker))}"],
                "args": {"seed": 1},
            }
        },
    )
    Experiment.create("trial", task=task, commit="a" * 40, config=config)
    monkeypatch.chdir(tmp_path)
    runner = CliRunner()

    inherited = runner.invoke(
        main, ["launch", "--dry-run", "trial:show", "--", "--seed", "3"]
    )

    assert inherited.exit_code == 0, inherited.output
    assert inherited.output.splitlines() == [
        f"Dispatcher: {config.dispatch_dir / 'launch.sh'} --commit {'a' * 40}",
        f"Command: /bin/sh -c 'touch {marker}' --seed 3",
    ]
    assert not marker.exists()

    explicit = runner.invoke(
        main, ["launch", "--dry-run", "--commit", "HEAD", "trial:show"]
    )
    assert explicit.exit_code == 0, explicit.output
    assert explicit.output.splitlines()[0] == (
        f"Dispatcher: {config.dispatch_dir / 'launch.sh'} --commit HEAD"
    )
    assert not marker.exists()


def test_launch_selector_controls_inheritance_and_attribution(
    tmp_path: Path, monkeypatch, capfd
) -> None:
    config = _project(tmp_path)
    task = Task.create(
        "demo",
        config=config,
        entrypoints={
            "shared": {
                "command": [
                    "/bin/sh",
                    "-c",
                    'printf "%s|task" "${HIRAKU_EXPERIMENT_ID:-}"',
                ]
            },
            "overridden": {"command": ["/bin/sh", "-c", "printf task"]},
        },
    )
    Experiment.create(
        "a",
        task=task,
        config=config,
        entrypoints={
            "overridden": {"command": ["/bin/sh", "-c", "printf experiment"]}
        },
    )
    Experiment.create("active-but-ignored", task=task, config=config)
    monkeypatch.chdir(tmp_path)
    runner = CliRunner()

    bare = runner.invoke(main, ["launch", "shared"])
    bare_output = capfd.readouterr().out
    inherited = runner.invoke(main, ["launch", "a:shared"])
    inherited_output = capfd.readouterr().out
    overridden = runner.invoke(main, ["launch", "a:overridden"])
    overridden_output = capfd.readouterr().out
    listed = runner.invoke(main, ["experiment", "entrypoints", "a", "--json"])

    assert bare.exit_code == 0 and bare_output == "|task"
    assert inherited.exit_code == 0 and inherited_output == "a|task"
    assert overridden.exit_code == 0 and overridden_output == "experiment"
    effective = json.loads(listed.output)
    assert effective["shared"]["source"] == "task"
    assert effective["overridden"]["source"] == "experiment"


def test_launch_override_matches_hyphens_and_underscores(
    tmp_path: Path, monkeypatch, capfd
) -> None:
    config = _project(tmp_path)
    Task.create(
        "demo",
        config=config,
        entrypoints={
            "score": {
                "command": [
                    "/bin/sh",
                    "-c",
                    'printf "%s|%s" "$1" "$2"',
                    "shell",
                ],
                "args": {"val-bpb": 1.0},
            }
        },
    )
    monkeypatch.chdir(tmp_path)

    result = CliRunner().invoke(
        main, ["launch", "score", "--", "--val-bpb", "0.9"]
    )

    assert result.exit_code == 0
    assert capfd.readouterr().out == "--val-bpb|0.9"


def test_run_json_is_complete_and_human_ids_are_unambiguous(
    tmp_path: Path, monkeypatch
) -> None:
    config = _project(tmp_path)
    task = Task.create(
        "demo",
        config=config,
        metrics=[{"name": "loss", "direction": "minimize"}],
    )
    Experiment.create("trial", task=task, config=config)
    commit = "a" * 40
    (config.root / "runs.csv").write_text(
        "id,task_id,experiment_id,commit,timestamp,metric.loss,param.seed,note\n"
        f"abcdefgh111,demo,trial,{commit},2026-01-02T00:00:00.123456Z,10,1,a\n"
        f"abcdefgh222,demo,trial,{commit},2026-01-01T00:00:00Z,2,2,b\n",
        encoding="utf-8",
    )
    monkeypatch.chdir(tmp_path)
    runner = CliRunner()

    result = runner.invoke(
        main, ["run", "list", "--sort", "loss", "--ascending", "--json"]
    )
    assert result.exit_code == 0, result.output
    runs = json.loads(result.output)
    assert [run["id"] for run in runs] == ["abcdefgh222", "abcdefgh111"]
    assert runs[0]["metrics"] == {"loss": "2"}
    assert runs[0]["params"] == {"seed": "2"}
    assert runs[0]["raw"] == {"note": "b"}
    assert runs[0]["timestamp"].endswith("+00:00")
    assert runs[0]["commit"] == commit

    human = runner.invoke(main, ["run", "list"])
    assert "abcdefgh1" in human.output
    assert "abcdefgh2" in human.output
    assert "2026-01-01 00:00:00Z" in human.output
    assert "2026-01-02 00:00:00Z" in human.output
    assert ".123456Z" not in human.output
    assert "aaaaaaaa" in human.output
    assert commit not in human.output
