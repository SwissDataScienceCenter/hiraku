from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from hiraku.config import HirakuConfig
from hiraku.dashboard.actions import (
    RecordCreateResult,
    RecordDraft,
    RecordDraftError,
    create_record,
    validate_record_draft,
)
from hiraku.dashboard.capability import DashboardCapability
from hiraku.errors import ValidationError
from hiraku.record import load_record


def _capability() -> DashboardCapability:
    return DashboardCapability(interactive=True)


def _init_project(tmp_path: Path, *, git: bool = False) -> HirakuConfig:
    if git:
        subprocess.run(["git", "init", "-q"], cwd=tmp_path, check=True)
        subprocess.run(
            [
                "git",
                "-c",
                "user.name=Test",
                "-c",
                "user.email=test@example.com",
                "commit",
                "--allow-empty",
                "-q",
                "-m",
                "init",
            ],
            cwd=tmp_path,
            check=True,
        )
    return HirakuConfig.init(tmp_path, install_skill=False)


def _project_bytes(config: HirakuConfig) -> tuple[dict[str, bytes], bytes]:
    records: dict[str, bytes] = {}
    if config.records_dir.is_dir():
        for path in sorted(config.records_dir.glob("*/*.md")):
            records[str(path.relative_to(config.root))] = path.read_bytes()
    return records, config.path.read_bytes()


def test_create_root_record_round_trips(tmp_path: Path) -> None:
    config = _init_project(tmp_path)
    before_records, before_yml = _project_bytes(config)
    draft = RecordDraft(
        id="root",
        type="task",
        summary="Improve accuracy",
        goal="Maximize val/accuracy",
        markdown="# Root",
    )
    result = create_record(config, draft, _capability())
    assert isinstance(result, RecordCreateResult)
    assert result.path == config.records_dir / "root/root.md"
    assert load_record("root", config=config).markdown == "# Root"
    assert load_record("root", config=config).to_dict()["summary"] == "Improve accuracy"
    after_records, after_yml = _project_bytes(config)
    assert before_yml == after_yml
    assert set(after_records) == {"hiraku/records/root/root.md"}
    assert before_records == {}


def test_create_child_and_nested_child_inherit(tmp_path: Path) -> None:
    config = _init_project(tmp_path)
    create_record(
        config,
        RecordDraft(id="root", summary="root summary", goal="root goal"),
        _capability(),
    )
    create_record(
        config,
        RecordDraft(id="child", parent_id="root", question="child question"),
        _capability(),
    )
    create_record(
        config,
        RecordDraft(id="nested", parent_id="child", type="experiment"),
        _capability(),
    )
    child = load_record("child", config=config)
    nested = load_record("nested", config=config)
    assert child.path == config.records_dir / "root/child.md"
    assert nested.path == config.records_dir / "root/nested.md"
    resolved = nested.resolve(config=config).values
    assert resolved["summary"] == "root summary"
    assert resolved["goal"] == "root goal"
    assert resolved["question"] == "child question"
    assert resolved["type"] == "experiment"


def test_create_does_not_change_active_record(tmp_path: Path) -> None:
    config = _init_project(tmp_path)
    create_record(config, RecordDraft(id="first"), _capability())
    assert HirakuConfig.load(config.path).active_record_id is None
    create_record(config, RecordDraft(id="second"), _capability())
    assert HirakuConfig.load(config.path).active_record_id is None


def test_capability_disabled_cannot_create(tmp_path: Path) -> None:
    config = _init_project(tmp_path)
    before_records, before_yml = _project_bytes(config)
    with pytest.raises(ValidationError, match="interactive mode"):
        create_record(config, RecordDraft(id="root"), DashboardCapability())
    assert _project_bytes(config) == (before_records, before_yml)


def test_duplicate_id_fails_without_writes(tmp_path: Path) -> None:
    config = _init_project(tmp_path)
    create_record(config, RecordDraft(id="root"), _capability())
    before_records, before_yml = _project_bytes(config)
    with pytest.raises(RecordDraftError) as caught:
        create_record(config, RecordDraft(id="root"), _capability())
    assert "already exists" in caught.value.errors["id"]
    assert _project_bytes(config) == (before_records, before_yml)


def test_path_traversal_id_fails(tmp_path: Path) -> None:
    config = _init_project(tmp_path)
    before_records, before_yml = _project_bytes(config)
    with pytest.raises(RecordDraftError):
        create_record(config, RecordDraft(id="../escape"), _capability())
    assert _project_bytes(config) == (before_records, before_yml)


def test_missing_parent_and_link_fail(tmp_path: Path) -> None:
    config = _init_project(tmp_path)
    create_record(config, RecordDraft(id="root"), _capability())
    with pytest.raises(RecordDraftError) as caught:
        create_record(
            config,
            RecordDraft(id="child", parent_id="missing"),
            _capability(),
        )
    assert "missing" in caught.value.errors["parent_id"]
    with pytest.raises(RecordDraftError) as caught:
        create_record(
            config,
            RecordDraft(id="child", related_to=("missing",)),
            _capability(),
        )
    assert "missing" in caught.value.errors["related_to"]


def test_commit_ref_resolves_and_invalid_commit_fails(tmp_path: Path) -> None:
    config = _init_project(tmp_path, git=True)
    head = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=tmp_path,
        text=True,
        capture_output=True,
        check=True,
    ).stdout.strip()
    result = create_record(
        config,
        RecordDraft(id="committed", commit_ref=head),
        _capability(),
    )
    assert result.record.commit == head

    before_records, before_yml = _project_bytes(config)
    with pytest.raises(RecordDraftError):
        create_record(
            config,
            RecordDraft(id="bad", commit_ref="not-a-ref"),
            _capability(),
        )
    assert _project_bytes(config) == (before_records, before_yml)


def test_advanced_yaml_validation(tmp_path: Path) -> None:
    config = _init_project(tmp_path)
    preview = validate_record_draft(
        config,
        RecordDraft(id="root", advanced_yaml="entrypoints: {train: {command: []}}"),
    )
    assert "advanced_yaml" in preview.errors

    before_records, before_yml = _project_bytes(config)
    with pytest.raises(RecordDraftError) as caught:
        create_record(
            config,
            RecordDraft(id="root", advanced_yaml="entrypoints: {train: {command: []}}"),
            _capability(),
        )
    assert "advanced_yaml" in caught.value.errors
    assert _project_bytes(config) == (before_records, before_yml)


def test_advanced_yaml_rejects_forbidden_and_colliding_fields(tmp_path: Path) -> None:
    config = _init_project(tmp_path)
    with pytest.raises(RecordDraftError) as caught:
        create_record(
            config,
            RecordDraft(id="root", advanced_yaml="path: elsewhere"),
            _capability(),
        )
    assert "path" in caught.value.errors["advanced_yaml"]

    with pytest.raises(RecordDraftError) as caught:
        create_record(
            config,
            RecordDraft(id="root", type="task", advanced_yaml="type: experiment"),
            _capability(),
        )
    assert "already provided" in caught.value.errors["advanced_yaml"]


def test_whitespace_advanced_yaml_is_omitted(tmp_path: Path) -> None:
    config = _init_project(tmp_path)
    result = create_record(
        config, RecordDraft(id="root", advanced_yaml="   \n"), _capability()
    )
    assert result.record.to_dict().get("advanced_yaml") is None


def test_pending_reparent_journal_blocks_mutation(tmp_path: Path) -> None:
    config = _init_project(tmp_path)
    journal = config.hiraku_dir / ".reparent-transaction.json"
    journal.write_text("{}", encoding="utf-8")
    before_records, before_yml = _project_bytes(config)
    with pytest.raises(ValidationError, match="not safe to modify"):
        create_record(config, RecordDraft(id="root"), _capability())
    assert _project_bytes(config) == (before_records, before_yml)


def test_invalid_metric_fails_without_writes(tmp_path: Path) -> None:
    config = _init_project(tmp_path)
    before_records, before_yml = _project_bytes(config)
    with pytest.raises(RecordDraftError):
        create_record(
            config,
            RecordDraft(
                id="root",
                advanced_yaml="metrics:\n  - name: loss\n    direction: sideways\n",
            ),
            _capability(),
        )
    assert _project_bytes(config) == (before_records, before_yml)
