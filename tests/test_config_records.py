from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from hiraku.agent import (
    Context,
    list_programs,
    load_action,
    load_context,
)
from hiraku.config import HirakuConfig
from hiraku.data import Experiment, Task, list_tasks, load_task
from hiraku.errors import (
    ProjectExistsError,
    ProjectNotFoundError,
    RecordExistsError,
    ValidationError,
)


def test_init_discovery_resources_and_skill_opt_out(tmp_path: Path) -> None:
    root = tmp_path / "project"
    config = HirakuConfig.init(root)
    nested = root / "src" / "module.py"
    nested.parent.mkdir()
    nested.write_text("")

    assert HirakuConfig.discover(root).root == root
    assert HirakuConfig.discover(nested).root == root
    assert (config.programs_dir / "interactive/program.md").is_file()
    assert (config.programs_dir / "autoresearch/program.md").is_file()
    assert (config.actions_dir / "experiment/propose/action.md").is_file()
    assert (config.dispatch_dir / "launch.sh").stat().st_mode & 0o111
    assert (root / ".agents/skills/hiraku/SKILL.md").is_file()
    assert config.active_task_id is None
    assert config.active_experiment_id is None

    without_skill = tmp_path / "without-skill"
    HirakuConfig.init(without_skill, install_skill=False)
    assert not (without_skill / ".agents").exists()

    with pytest.raises(ProjectExistsError):
        HirakuConfig.init(root)
    with pytest.raises(ProjectNotFoundError):
        HirakuConfig.discover(tmp_path / "missing")


def test_config_preserves_unknown_keys_and_active_invariants(tmp_path: Path) -> None:
    config = HirakuConfig.init(tmp_path)
    raw = yaml.safe_load(config.path.read_text())
    raw["extension"] = {"enabled": True}
    config.path.write_text(yaml.safe_dump(raw, sort_keys=False))
    config = HirakuConfig.discover(tmp_path)
    first = Task.create("first", config=config)
    experiment = Experiment.create("trial", task=first, config=config)
    second = Task.create("second", config=config)

    assert config.active_task_id == second.id
    assert config.active_experiment_id is None
    config.set_active_experiment(experiment)
    assert (config.active_task_id, config.active_experiment_id) == ("first", "trial")
    config.clear_active_experiment()
    assert yaml.safe_load(config.path.read_text())["extension"] == {"enabled": True}


def test_hiraku_dir_rejects_traversal_and_symlink_escape(tmp_path: Path) -> None:
    marker = tmp_path / "hiraku.yml"
    marker.write_text("hiraku_dir: ../outside\n")
    with pytest.raises(ValidationError):
        HirakuConfig.load(marker)

    outside = tmp_path.parent / f"{tmp_path.name}-outside"
    outside.mkdir()
    (tmp_path / "linked").symlink_to(outside, target_is_directory=True)
    marker.write_text("hiraku_dir: linked\n")
    with pytest.raises(ValidationError):
        HirakuConfig.load(marker)


def test_markdown_round_trip_order_conflicts_and_identity(tmp_path: Path) -> None:
    config = HirakuConfig.init(tmp_path)
    task = Task(
        id="sample", markdown="# Body\n\nVerbatim.\n", zeta=1, alpha={"x": True}
    )
    path = task.save(config.tasks_dir / "sample/task.md")
    loaded = Task.load(path)

    assert list(loaded.to_dict()) == ["id", "zeta", "alpha"]
    assert loaded.markdown == "# Body\n\nVerbatim.\n"
    loaded.new = [1, 2]
    loaded.save()
    assert Task.load(path).to_dict()["new"] == [1, 2]
    assert load_task("sample", config=config).id == "sample"
    assert [record.id for record in list_tasks(config=config)] == ["sample"]
    assert [record.id for record in Task.list(config.tasks_dir.glob("*/task.md"))] == [
        "sample"
    ]
    with pytest.raises(ValidationError):
        loaded.id = "renamed"
    detached = Task(id="sample")
    with pytest.raises(ValidationError):
        detached.save()
    with pytest.raises(RecordExistsError):
        detached.save(path)
    with pytest.raises(ValidationError):
        loaded.save(config.tasks_dir / "moved/task.md")


@pytest.mark.parametrize(
    "content",
    ["id: no-front-matter\n", "---\n- one\n- two\n---\n", "---\nid: [\n---\n"],
)
def test_malformed_records_are_rejected(tmp_path: Path, content: str) -> None:
    config = HirakuConfig.init(tmp_path)
    path = config.tasks_dir / "bad/task.md"
    path.parent.mkdir()
    path.write_text(content)
    with pytest.raises(ValidationError):
        Task.load(path)


def test_agent_record_registries(tmp_path: Path) -> None:
    config = HirakuConfig.init(tmp_path)
    assert [record.id for record in list_programs(config=config)] == [
        "autoresearch",
        "interactive",
    ]
    context = Context(id="literature", markdown="Sources")
    context.save(config.contexts_dir / "literature/context.md")
    assert load_context("literature", config=config).markdown == "Sources"
    action = load_action("propose", entity="experiment", config=config)
    assert action.entity == "experiment"
