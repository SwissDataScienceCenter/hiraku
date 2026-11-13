from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tomllib
from importlib.resources import files
from pathlib import Path

import pytest
from pydantic import BaseModel
from pydantic import ValidationError as PydanticValidationError

import hiraku.project
from hiraku.errors import (
    ProjectExistsError,
    ProjectNotFoundError,
    ValidationError,
)
from hiraku.project import Project
from hiraku.record import Record
from hiraku.tracking import CSVBackend, Leaderboard


def test_project_create_can_skip_mcp_configuration(tmp_path: Path) -> None:
    project = Project.create(tmp_path, install_skill=False, install_mcp=False)

    assert project.psij_dir == tmp_path / ".hiraku" / "psij"
    assert not project.psij_dir.exists()
    assert project.leaderboards_dir == tmp_path / "hiraku" / "leaderboards"
    assert list(project.leaderboards_dir.iterdir()) == []
    assert project.active_record_id is None
    assert project.records_dir == tmp_path / "hiraku" / "records"
    assert not (tmp_path / ".mcp.json").exists()
    assert not (tmp_path / ".codex/config.toml").exists()


def test_create_rejects_configuration_directory_before_writing(tmp_path: Path) -> None:
    marker = tmp_path / "hiraku.yml"
    marker.mkdir()

    with pytest.raises(ProjectExistsError):
        Project.create(tmp_path)

    assert list(tmp_path.iterdir()) == [marker]


def test_project_uses_pydantic_validation_and_forbids_unknown_settings(
    tmp_path: Path,
) -> None:
    project = Project(tmp_path)
    marker = tmp_path / "hiraku.yml"
    marker.write_text("unknown_setting: true\n")

    assert isinstance(project, BaseModel)
    with pytest.raises(ValidationError, match="Invalid project configuration"):
        Project.load(marker)


def test_project_validates_settings_on_creation(tmp_path: Path) -> None:
    with pytest.raises(PydanticValidationError, match="parent traversal"):
        Project(tmp_path, hiraku_dir_name="../outside")

    with pytest.raises(PydanticValidationError, match="default_dispatch"):
        Project(tmp_path, default_dispatch="")


def test_init_leaves_client_hooks_out_of_the_project(tmp_path):
    existing = {"matcher": "Write", "hooks": [{"type": "command", "command": "check"}]}
    for relative in (".codex/hooks.json", ".claude/settings.json"):
        path = tmp_path / relative
        path.parent.mkdir(exist_ok=True)
        path.write_text(
            json.dumps({"other": True, "hooks": {"PreToolUse": [existing]}})
        )
    Project.create(tmp_path, install_skill=False)
    for relative in (".codex/hooks.json", ".claude/settings.json"):
        path = tmp_path / relative
        assert json.loads(path.read_text()) == {
            "other": True,
            "hooks": {"PreToolUse": [existing]},
        }


@pytest.mark.parametrize("locked", [False, True])
def test_init_uses_uv_when_the_project_has_a_lockfile(tmp_path, locked):
    (tmp_path / "pyproject.toml").write_text(
        '[project]\nname = "x"\ndependencies = ["hiraku"]\n'
    )
    if locked:
        (tmp_path / "uv.lock").touch()
    launcher = ["uv", "run", "hiraku"] if locked else ["hiraku"]
    Project.create(tmp_path, install_skill=False)
    server = json.loads((tmp_path / ".mcp.json").read_text())["mcpServers"]["hiraku"]
    assert [server["command"], *server["args"]] == [*launcher, "mcp", "serve"]
    codex = tomllib.loads((tmp_path / ".codex/config.toml").read_text())
    assert codex["mcp_servers"]["hiraku"] == server
    for config in (tmp_path / ".mcp.json", tmp_path / ".pi/mcp.json"):
        assert json.loads(config.read_text())["mcpServers"] == {"hiraku": server}
    assert codex["mcp_servers"] == {"hiraku": server}
    assert not (tmp_path / ".claude/settings.json").exists()


@pytest.mark.parametrize(
    "content", ["not json", "[]", '{"hooks": []}', '{"hooks":{"PreToolUse":{}}}']
)
def test_init_preserves_hook_configuration_without_inspecting_it(tmp_path, content):
    path = tmp_path / ".claude/settings.json"
    path.parent.mkdir()
    path.write_text(content)
    Project.create(tmp_path, install_skill=False)
    assert path.read_text() == content
    assert (tmp_path / "hiraku.yml").is_file()
    assert (tmp_path / ".mcp.json").is_file()


def _git(path: Path, *arguments: str) -> None:
    subprocess.run(
        ["git", *arguments], cwd=path, text=True, capture_output=True, check=True
    )


def test_init_without_repository_or_at_nested_directory(tmp_path: Path) -> None:
    config = Project.create(tmp_path / "plain")
    assert config.root == tmp_path / "plain"

    repository = tmp_path / "repository"
    repository.mkdir()
    _git(repository, "init", "-q")
    nested = repository / "nested"
    config = Project.create(nested)
    assert config.root == nested


def test_init_discovery_resources_and_skill_opt_out(tmp_path: Path) -> None:
    root = tmp_path / "project"
    config = Project.create(root)
    nested = root / "src" / "module.py"
    nested.parent.mkdir()
    nested.write_text("")

    assert Project.discover(root).root == root
    assert Project.discover(nested).root == root
    assert sorted(path.name for path in config.workflows_dir.iterdir()) == [
        "collaborative"
    ]
    assert (config.workflows_dir / "collaborative" / "workflow.py").is_file()
    assert (config.workflows_dir / "collaborative" / "workflow.md").is_file()
    assert not (config.hiraku_dir / "programs").exists()
    assert not (config.hiraku_dir / "actions").exists()
    assert not hasattr(config, "default_dispatch")
    codex_skill = root / ".agents/skills/hiraku/SKILL.md"
    claude_skill = root / ".claude/skills/hiraku/SKILL.md"
    assert codex_skill.is_file()
    assert claude_skill.is_file()
    assert not (root / ".pi/skills").exists()
    assert not (root / ".gemini/skills").exists()
    assert codex_skill.read_text() == claude_skill.read_text()
    assert codex_skill.read_text() == (
        files("hiraku.resources").joinpath("skills/hiraku/SKILL.md").read_text()
    )
    assert config.active_record_id is None

    without_skill = tmp_path / "without-skill"
    Project.create(without_skill, install_skill=False)
    assert not (without_skill / ".agents").exists()
    assert not (without_skill / ".claude/skills").exists()
    assert not (without_skill / ".claude/settings.json").exists()

    with pytest.raises(ProjectExistsError):
        Project.create(root)
    with pytest.raises(ProjectNotFoundError):
        Project.discover(tmp_path / "missing")


def test_init_installs_all_bundled_skill_files(tmp_path: Path) -> None:
    Project.create(tmp_path)
    source = Path(str(files("hiraku.resources").joinpath("skills")))
    expected = {
        path.relative_to(source): path.read_bytes()
        for path in source.rglob("*")
        if path.is_file()
    }
    assert expected
    for agent in (".agents", ".claude"):
        destination = tmp_path / agent / "skills"
        actual = {
            path.relative_to(destination): path.read_bytes()
            for path in destination.rglob("*")
            if path.is_file()
        }
        assert actual == expected


def test_init_from_template_applies_overlay(tmp_path: Path) -> None:
    template = tmp_path / "template"
    (template / "custom/dispatch").mkdir(parents=True)
    (template / "empty").mkdir()
    (template / "hiraku").mkdir()
    (template / "hiraku.yml").write_text("hiraku_dir: custom\n")
    launcher = template / "custom/dispatch/launch.sh"
    launcher.write_text("#!/bin/sh\necho template\n")
    launcher.chmod(0o744)
    (template / ".example-dotfile").write_text("included\n")
    (template / "payload.bin").write_bytes(b"\x00\xfftemplate")

    root = tmp_path / "project"
    config = Project.create(
        root,
        template=template,
        install_skill=False,
    )

    assert config.hiraku_dir == root / "custom"
    assert launcher.read_text() == (root / "custom/dispatch/launch.sh").read_text()
    assert (root / "custom/dispatch/launch.sh").stat().st_mode & 0o111
    assert not (root / "custom/dispatch/worktree.sh").exists()
    assert (root / ".example-dotfile").read_text() == "included\n"
    assert (root / "payload.bin").read_bytes() == b"\x00\xfftemplate"
    assert (root / "empty").is_dir()


def test_init_from_packaged_example_copies_only_src(
    tmp_path: Path, monkeypatch
) -> None:
    resources = tmp_path / "resources"
    for name in ("workflows", "dispatch"):
        (resources / name).mkdir(parents=True)
    example = resources / "examples/demo"
    (example / "src").mkdir(parents=True)
    (example / "README.md").write_text("do not copy")
    (example / "example.yml").write_text("description: Demo\n")
    (example / "src/copied.txt").write_text("copied")
    monkeypatch.setattr(hiraku.project, "files", lambda package: resources)

    root = tmp_path / "project"
    Project.create(root, example="demo", install_skill=False)

    assert (root / "copied.txt").read_text() == "copied"
    assert not (root / "README.md").exists()
    assert not (root / "example.yml").exists()
    with pytest.raises(ValidationError, match="available examples: demo"):
        Project.create(tmp_path / "unknown", example="missing")


def test_init_rejects_conflicting_template_sources(tmp_path: Path) -> None:
    template = tmp_path / "template"
    template.mkdir()

    with pytest.raises(ValidationError, match="mutually exclusive"):
        Project.create(tmp_path / "project", template=template, example="demo")


def test_load_project_through_symlinked_directory(tmp_path: Path) -> None:
    project = Project.create(
        tmp_path / "project", install_skill=False, install_mcp=False
    )
    linked = tmp_path / "linked"
    linked.symlink_to(project.root, target_is_directory=True)

    loaded = Project.load(linked / "hiraku.yml")

    assert loaded.root == project.root
    assert loaded.path == project.path


def test_active_record_is_stored_in_project_config(tmp_path: Path) -> None:
    config = Project.create(tmp_path, install_skill=False, install_mcp=False)
    first = config.vault.create("first/first", type="task", title="first")
    config.set_active_record(first.id)
    assert Project.load(config.path).active_record_id == first.id
    config.clear_active_record()
    assert Project.load(config.path).active_record_id is None
    config.set_active_record(first)

    assert config.path == tmp_path / "hiraku.yml"
    assert f"active_record_id: {first.id}" in config.path.read_text()
    assert Project.load(config.path).active_record_id == first.id
    assert set(config.to_dict()) == {
        "hiraku_dir",
        "active_record_id",
    }


def test_hiraku_dir_rejects_traversal_and_symlink_escape(tmp_path: Path) -> None:
    marker = tmp_path / "hiraku.yml"
    marker.write_text("hiraku_dir: ../outside\n")
    with pytest.raises(ValidationError):
        Project.load(marker)

    outside = tmp_path.parent / f"{tmp_path.name}-outside"
    outside.mkdir()
    (tmp_path / "linked").symlink_to(outside, target_is_directory=True)
    marker.write_text("hiraku_dir: linked\n")
    with pytest.raises(ValidationError):
        Project.load(marker)


def test_init_adds_hiraku_to_existing_codex_configuration(tmp_path: Path) -> None:
    codex = tmp_path / ".codex" / "config.toml"
    codex.parent.mkdir()
    codex.write_text('model = "x"\n\n[mcp_servers.other]\ncommand = "other"\n')
    Project.create(tmp_path, install_skill=False)
    parsed = tomllib.loads(codex.read_text())
    assert parsed["model"] == "x"
    assert parsed["mcp_servers"]["other"] == {"command": "other"}
    assert parsed["mcp_servers"]["hiraku"] == {
        "command": "hiraku",
        "args": ["mcp", "serve"],
    }


def test_init_preserves_existing_mcp_servers(tmp_path: Path) -> None:
    existing = {"command": "custom-server", "args": ["serve"]}
    for relative in (".mcp.json", ".pi/mcp.json"):
        path = tmp_path / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps({"mcpServers": {"other": existing}}))
    codex = tmp_path / ".codex/config.toml"
    codex.parent.mkdir()
    codex.write_text(
        '[mcp_servers.other]\ncommand = "custom-server"\nargs = ["serve"]\n'
    )

    Project.create(tmp_path, install_skill=False)

    for relative in (".mcp.json", ".pi/mcp.json"):
        config = json.loads((tmp_path / relative).read_text())
        assert config["mcpServers"]["other"] == existing
    assert tomllib.loads(codex.read_text())["mcp_servers"]["other"] == existing


def test_init_leaves_inline_codex_mcp_servers_unchanged(tmp_path: Path) -> None:
    codex = tmp_path / ".codex" / "config.toml"
    codex.parent.mkdir()
    original = 'mcp_servers = { other = { command = "other" } }\n'
    codex.write_text(original)
    with pytest.raises(ValidationError, match=r"\[mcp_servers.hiraku\] table"):
        Project.create(tmp_path, install_skill=False)
    assert codex.read_text() == original


def test_base_example_has_no_results(tmp_path: Path):
    config = Project.create(tmp_path, example="heart-disease", install_skill=False)
    assert (tmp_path / "train.py").is_file()
    assert not (tmp_path / "runs.csv").exists()
    assert not list(config.records_dir.rglob("*.md"))


@pytest.mark.parametrize("example", [None, "minimal"])
def test_unavailable_results_leave_no_project(tmp_path: Path, example):
    with pytest.raises(ValidationError, match="requires|no results"):
        Project.create(tmp_path, example=example, results=True)
    assert not (tmp_path / "hiraku.yml").exists()


def test_results_include_leaderboard_configuration(tmp_path: Path):
    config = Project.create(
        tmp_path,
        example="digits-mlflow",
        results=True,
        install_skill=False,
    )
    assert (
        config.active_record_id == config.vault.load("digit-classification/weak-l2").id
    )
    assert Leaderboard.load(config).source.model_dump(
        mode="json", exclude_none=True
    ) == {
        "type": "mlflow",
        "database": "mlflow.db",
        "experiment_name": "hiraku-digits",
    }
    assert config.vault.load("digit-classification").id == "digit-classification"


def test_results_override_base_files(tmp_path: Path, monkeypatch):
    resources = tmp_path / "resources"
    base = resources / "examples/layered/src"
    results = resources / "examples/layered/results"
    (resources / "workflows").mkdir(parents=True)
    base.mkdir(parents=True)
    results.mkdir()
    (base / "train.py").write_text("base")
    (base / "hiraku.yml").write_text("{}\n")
    (results / "train.py").write_text("results")
    (results / "hiraku.yml").write_text("active_record_id: root\n")
    monkeypatch.setattr(hiraku.project, "files", lambda name: resources)
    target = tmp_path / "target"
    config = Project.create(
        target, example="layered", results=True, install_skill=False
    )
    assert (target / "train.py").read_text() == "results"
    assert config.active_record_id == "root"
    assert "active_record_id: root" in config.path.read_text()


RUN_AGENT_TESTS = os.environ.get("HIRAKU_RUN_CODEX_SKILL_TESTS") == "1"

MODEL = "gpt-5.6-luna"
TIMEOUT_SECONDS = 300
EXPECTED_LOSS = 0.32254890
EXPECTED_ACCURACY = 0.85526316


def _skill_git(project: Path, *arguments: str) -> str:
    result = subprocess.run(
        ["git", *arguments],
        cwd=project,
        text=True,
        capture_output=True,
        check=True,
    )
    return result.stdout.strip()


def _new_project(tmp_path: Path) -> tuple[Path, Project, str]:
    project = tmp_path / "heart-disease"
    project.mkdir()
    _skill_git(project, "init", "-b", "main")
    _skill_git(project, "config", "user.email", "test@example.invalid")
    _skill_git(project, "config", "user.name", "Test")
    config = Project.create(
        project,
        example="heart-disease",
    )
    _skill_git(project, "add", ".")
    _skill_git(project, "commit", "-m", "initialize Heart Disease example")
    commit = _skill_git(project, "rev-parse", "HEAD")
    return project, config, commit


def _run_codex(project: Path, prompt: str) -> str:
    codex = shutil.which("codex")
    hiraku = shutil.which("hiraku")
    if codex is None:
        pytest.fail("codex is not on PATH")
    if hiraku is None:
        pytest.fail("hiraku is not on PATH")

    environment = os.environ.copy()
    environment["PATH"] = os.pathsep.join(
        [str(Path(sys.executable).parent), environment.get("PATH", "")]
    )
    command = [
        codex,
        "exec",
        "--ephemeral",
        "--ignore-user-config",
        "--model",
        MODEL,
        "--config",
        'model_reasoning_effort="low"',
        "--approve-for-me",
        "--color",
        "never",
        "--cd",
        str(project),
        prompt,
    ]
    try:
        result = subprocess.run(
            command,
            cwd=project,
            env=environment,
            text=True,
            capture_output=True,
            timeout=TIMEOUT_SECONDS,
            check=False,
        )
    except subprocess.TimeoutExpired as exc:
        pytest.fail(
            f"Codex {MODEL} timed out after {TIMEOUT_SECONDS} seconds.\n"
            f"stdout:\n{exc.stdout or ''}\n"
            f"stderr:\n{exc.stderr or ''}"
        )
    if result.returncode != 0:
        pytest.fail(
            f"Codex {MODEL} exited with status {result.returncode}. "
            "Check local authentication and model availability.\n"
            f"stdout:\n{result.stdout}\n"
            f"stderr:\n{result.stderr}"
        )
    return result.stdout


def _create_task(project: Project) -> Record:
    record = project.vault.create(
        "heart-disease-prediction",
        type="task",
        branch="main",
        goal="Develop and evaluate predictive models for heart disease "
        "on a fixed Cleveland cohort",
        question="Which modeling approaches achieve the best validation log loss "
        "and accuracy on this cohort?",
        title="Heart disease prediction",
    )
    project.set_active_record(record)
    return record


def _create_logistic_experiment(
    project: Project,
    task: Record,
    commit: str,
) -> Record:
    record = project.vault.create(
        f"{task.id}/logistic-regression",
        type="task",
        parent_id=task.id,
        commit=commit,
        summary="Logistic regression baseline",
        hypothesis="Regularized logistic regression provides a calibrated baseline",
        title="Logistic regression",
    )
    project.set_active_record(record)
    return record


@pytest.mark.skipif(
    not RUN_AGENT_TESTS,
    reason="set HIRAKU_RUN_CODEX_SKILL_TESTS=1 to run Codex skill tests",
)
def test_agent_creates_task_from_high_level_request(tmp_path: Path) -> None:
    project, config, _ = _new_project(tmp_path)

    agent_output = _run_codex(
        project,
        """Use the $hiraku skill to create a root record named heart-disease.
Its goal is
to develop and evaluate predictive models for heart disease on the fixed
Cleveland cohort without restricting the task to particular model families. Ask
which modeling approaches achieve the best validation log loss and accuracy.
Track val/log_loss to minimize and val/accuracy to maximize. Only create and
verify the record and its leaderboard.""",
    )

    tasks = config.vault.list()
    assert len(tasks) == 1, agent_output
    task = tasks[0]
    assert task.title == "heart-disease"
    assert task.goal is not None
    goal = task.goal.lower()
    assert "predictive" in goal
    assert "heart disease" in goal
    assert "cleveland" in goal
    assert task.question is not None
    question = task.question.lower()
    assert "model" in question
    assert "log loss" in question
    assert "accuracy" in question
    assert [metric.model_dump() for metric in Leaderboard.load(config).metrics] == [
        {"name": "val/log_loss", "direction": "minimize"},
        {"name": "val/accuracy", "direction": "maximize"},
    ]
    assert config.vault.children(task.id) == []
    assert CSVBackend(config, path="runs.csv").list_runs(record_id=task.id) == []
    assert _skill_git(project, "status", "--porcelain") == ""


@pytest.mark.skipif(
    not RUN_AGENT_TESTS,
    reason="set HIRAKU_RUN_CODEX_SKILL_TESTS=1 to run Codex skill tests",
)
def test_agent_creates_experiment_from_high_level_request(tmp_path: Path) -> None:
    project, config, commit = _new_project(tmp_path)
    task = _create_task(config)

    agent_output = _run_codex(
        project,
        """Use the $hiraku skill to create the record
heart-disease-prediction/logistic-regression with title 'Logistic regression'
under heart-disease-prediction. Use the summary 'Logistic regression baseline' and the
hypothesis 'Regularized logistic regression provides a calibrated baseline'.
Bind it to the current commit. Only create and verify the record; do not run it.""",
    )

    experiments = config.vault.children(task.id)
    assert len(experiments) == 1, agent_output
    experiment = experiments[0]
    assert experiment.title == "Logistic regression"
    assert experiment.summary == "Logistic regression baseline"
    assert experiment.hypothesis == (
        "Regularized logistic regression provides a calibrated baseline"
    )
    assert experiment.commit == commit
    assert CSVBackend(config, path="runs.csv").list_runs(record_id=task.id) == []
    assert _skill_git(project, "status", "--porcelain") == ""


@pytest.mark.skipif(
    not RUN_AGENT_TESTS,
    reason="set HIRAKU_RUN_CODEX_SKILL_TESTS=1 to run Codex skill tests",
)
def test_agent_runs_existing_experiment_without_other_changes(tmp_path: Path) -> None:
    project, config, commit = _new_project(tmp_path)
    task = _create_task(config)
    experiment = _create_logistic_experiment(config, task, commit)
    task_before = config.vault.file_path(task.id).read_bytes()
    experiment_before = config.vault.file_path(experiment.id).read_bytes()

    agent_output = _run_codex(
        project,
        """Use the $hiraku skill to run the Logistic regression record and
report its tracked validation metrics. Do not create or update other records.""",
    )

    assert config.vault.file_path(task.id).read_bytes() == task_before, agent_output
    current = config.vault.load(experiment.id)
    assert config.vault.file_path(current.id).read_bytes() == experiment_before
    assert [item.title for item in config.vault.children(task.id)] == [
        "Logistic regression"
    ]
    runs = CSVBackend(config, path="runs.csv").list_runs(
        record_id=experiment.id,
    )
    assert len(runs) == 1
    run = runs[0]
    assert run.record_id == experiment.id
    assert run.commit == commit
    assert run.id
    assert run.params == {
        "model": "logistic",
        "seed": "42",
        "validation_fraction": "0.25",
    }
    assert float(run.metrics["val/log_loss"]) == pytest.approx(EXPECTED_LOSS)
    assert float(run.metrics["val/accuracy"]) == pytest.approx(EXPECTED_ACCURACY)
    assert _skill_git(project, "status", "--porcelain") == ""
