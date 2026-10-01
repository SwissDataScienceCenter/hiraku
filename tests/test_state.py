import json
from pathlib import Path

import pytest
from click.testing import CliRunner

from hiraku.base import BaseRecord
from hiraku.cli import main
from hiraku.config import HirakuConfig
from hiraku.errors import ValidationError
from hiraku.record import Record, load_record
from hiraku.state import RecordType, list_types, load_type, validate_transition


@pytest.mark.parametrize(
    ("record_type", "statuses"),
    [
        ("task", ["draft", "active", "completed"]),
        ("experiment", ["draft", "implemented", "running", "ready", "completed"]),
        ("dataset", ["draft", "processing", "ready"]),
    ],
)
def test_bundled_type_defaults_and_labels(
    tmp_path: Path, record_type: str, statuses: list[str]
) -> None:
    config = HirakuConfig.init(tmp_path, install_skill=False)
    definition = load_type(record_type, config=config)
    assert isinstance(definition, BaseRecord)
    assert definition.lifecycle == statuses
    record = Record.create("sample", type=record_type, config=config)
    assert record.status == "draft"
    for source, destination in zip(statuses, statuses[1:], strict=False):
        validate_transition(record_type, source, destination, config=config)
    validate_transition(record_type, "draft", statuses[-1], config=config)


def test_project_type_override_and_custom_type(tmp_path: Path) -> None:
    config = HirakuConfig.init(tmp_path, install_skill=False)
    (config.types_dir / "experiment.yml").write_text(
        "id: experiment\ninitial_status: draft\n"
        "lifecycle: [draft, reviewed, concluded]\n"
    )
    (config.types_dir / "survey.yml").write_text(
        "id: survey\ninitial_status: draft\nlifecycle: [draft, collected]\n"
    )
    assert Record.create("custom", type="survey", config=config).status == "draft"
    assert load_type("experiment", config=config).lifecycle == [
        "draft",
        "reviewed",
        "concluded",
    ]
    definition = load_type("survey", config=config)
    definition.markdown = "Saved type description"
    definition.save()
    assert load_type("survey", config=config).markdown == "Saved type description"
    assert (
        Record.create(
            "manual", type="experiment", status="custom", config=config
        ).status
        == "custom"
    )
    with pytest.raises(ValidationError, match="No lifecycle"):
        Record.create("unknown", type="missing", config=config)


def test_manual_status_can_be_arbitrary_but_action_transition_is_checked(
    tmp_path: Path,
) -> None:
    config = HirakuConfig.init(tmp_path, install_skill=False)
    record = Record.create("sample", type="experiment", config=config)
    record.status = "paused_by_user"
    record.save()
    assert load_record("sample", config=config).status == "paused_by_user"
    with pytest.raises(ValidationError, match="Invalid experiment transition"):
        validate_transition(
            "experiment", "paused_by_user", "implemented", config=config
        )


def test_invalid_project_lifecycle_is_rejected(tmp_path: Path) -> None:
    config = HirakuConfig.init(tmp_path, install_skill=False)
    (config.types_dir / "experiment.yml").write_text(
        "id: experiment\ninitial_status: draft\nlifecycle: [draft, draft]\n"
    )
    with pytest.raises(ValidationError, match="non-empty and unique"):
        load_type("experiment", config=config)


def test_type_record_roundtrip_and_cli(tmp_path: Path, monkeypatch) -> None:
    config = HirakuConfig.init(tmp_path, install_skill=False)
    definition = RecordType(
        id="experiment",
        initial_status="draft",
        lifecycle=["reviewed", "concluded"],
        markdown="Local type",
    )
    definition.save(config.types_dir / "experiment.md")
    RecordType(id="survey", initial_status="draft", lifecycle=["collected"]).save(
        config.types_dir / "survey.md"
    )
    assert load_type("experiment", config=config).markdown == "Local type"
    assert [item.id for item in list_types(config=config)] == [
        "dataset",
        "experiment",
        "survey",
        "task",
    ]
    monkeypatch.chdir(tmp_path)
    runner = CliRunner()
    listing = runner.invoke(main, ["type", "list", "--json"])
    assert listing.exit_code == 0, listing.output
    assert len(json.loads(listing.output)) == 4
    shown = runner.invoke(main, ["type", "show", "experiment"])
    assert shown.exit_code == 0, shown.output
    assert "Local type" in shown.output
    shown = runner.invoke(main, ["type", "show", "experiment", "--json"])
    assert json.loads(shown.output)["lifecycle"] == ["reviewed", "concluded"]
    missing = runner.invoke(main, ["type", "show", "missing"])
    assert missing.exit_code != 0
    assert "No lifecycle" in missing.output
