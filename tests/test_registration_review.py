"""Manifest validation, review planning, and import CLI tests."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest
from click.testing import CliRunner

from hiraku.cli import main
from hiraku.config import HirakuConfig
from hiraku.errors import ValidationError
from hiraku.io import dump_yaml
from hiraku.record import list_records
from hiraku.registration import (
    RegistrationManifest,
    apply_import,
    load_manifest,
    plan_import,
)


@pytest.fixture
def project(tmp_path: Path) -> HirakuConfig:
    return HirakuConfig.init(tmp_path, install_skill=False)


def _manifest(**overrides) -> RegistrationManifest:
    data = {
        "source": "filesystem",
        "scope": {"coverage": "exhaustive"},
        "datasets": [
            {
                "namespace": "filesystem",
                "key": ["data"],
                "coverage": "exhaustive",
                "artifacts": [
                    {
                        "key": "data/raw.csv",
                        "display_path": "data/raw.csv",
                        "revision": "123:456",
                        "size": 42,
                        "availability": "present",
                    }
                ],
            }
        ],
    }
    data.update(overrides)
    return RegistrationManifest.model_validate(data)


def _write_manifest(path: Path, manifest: RegistrationManifest) -> None:
    path.write_text(dump_yaml(manifest.model_dump(mode="json")), encoding="utf-8")


def test_hiraku_help_lists_registry() -> None:
    result = CliRunner().invoke(main, ["--help"])

    assert result.exit_code == 0, result.output
    assert "registry" in result.output
    assert "snakemake" in result.output


def test_preview_is_non_mutating(project: HirakuConfig) -> None:
    plan = plan_import(_manifest(), project)

    assert plan.applied is False
    assert plan.creates
    assert list_records(config=project) == []


def test_apply_creates_dataset_records(project: HirakuConfig) -> None:
    manifest = _manifest()
    plan = apply_import(manifest, project)

    assert plan.applied is True
    records = list_records(config=project)
    assert [record.id for record in records] == plan.creates
    dataset = records[0]
    assert dataset.type == "dataset"
    assert dataset.to_dict()["registration"]["artifacts"][0]["display_path"] == (
        "data/raw.csv"
    )


def test_apply_twice_is_idempotent(project: HirakuConfig) -> None:
    manifest = _manifest()
    apply_import(manifest, project)
    again = apply_import(manifest, project)

    assert again.creates == []
    assert again.updates == []
    assert again.unchanged == 1
    assert len(list_records(config=project)) == 1


def test_tampered_hash_is_rejected(tmp_path: Path) -> None:
    manifest = _manifest()
    manifest.content_sha256 = "0" * 40
    path = tmp_path / "manifest.yml"
    _write_manifest(path, manifest)

    with pytest.raises(ValidationError, match="content_sha256"):
        load_manifest(path)


def test_unknown_schema_is_rejected(project: HirakuConfig) -> None:
    with pytest.raises(ValidationError, match="schema_version"):
        apply_import(_manifest(schema_version=2), project)


def test_duplicate_identity_is_rejected(project: HirakuConfig) -> None:
    datasets = [
        {"namespace": "n", "key": "k", "coverage": "exhaustive"},
        {"namespace": "n", "key": "k", "coverage": "exhaustive"},
    ]
    with pytest.raises(ValidationError, match="duplicate dataset identities"):
        apply_import(_manifest(datasets=datasets), project)


def test_descriptor_only_remote_does_not_open_locations(
    project: HirakuConfig,
) -> None:
    manifest = _manifest(
        source="remote",
        datasets=[
            {
                "namespace": "remote",
                "key": "set-a",
                "coverage": "unknown",
                "artifacts": [
                    {
                        "key": "set-a/scan.tiff",
                        "display_path": "scans/scan.tiff",
                        "access": {
                            "uri": "https://nonexistent.invalid/scans/scan.tiff"
                        },
                    }
                ],
            }
        ],
    )

    plan = apply_import(manifest, project)

    assert plan.applied is True
    assert len(list_records(config=project)) == 1


def test_omitted_optional_metadata_is_allowed(project: HirakuConfig) -> None:
    manifest = _manifest(datasets=[{"namespace": "plain", "key": "single"}])

    plan = apply_import(manifest, project)

    assert plan.applied is True
    assert list_records(config=project)[0].to_dict()["registration"]["metadata"] == {}


def test_reviewed_hash_mismatch_blocks_apply(project: HirakuConfig) -> None:
    manifest = _manifest()
    manifest.content_sha256 = manifest.compute_digest()

    with pytest.raises(ValidationError, match="reviewed hash"):
        apply_import(manifest, project, reviewed_hash="0" * 40)

    assert list_records(config=project) == []


def test_apply_accepts_matching_reviewed_hash(project: HirakuConfig) -> None:
    manifest = _manifest()
    manifest.content_sha256 = manifest.compute_digest()

    plan = apply_import(manifest, project, reviewed_hash=manifest.content_sha256)

    assert plan.applied is True


def test_conflicting_record_id_is_reported(project: HirakuConfig) -> None:
    from hiraku.record import Record

    manifest = _manifest()
    # Occupy the derived dataset record ID with an unrelated record.
    Record.create(manifest.datasets[0].record_id(), type="experiment", config=project)

    plan = plan_import(manifest, project)

    assert plan.conflicts
    assert plan.creates == []


def test_collection_parent_is_created_first(project: HirakuConfig) -> None:
    manifest = _manifest(
        datasets=[
            {
                "namespace": "filesystem",
                "key": ["data"],
                "parent": "cohort-a",
                "coverage": "exhaustive",
                "artifacts": [
                    {
                        "key": "a",
                        "display_path": "a.csv",
                        "availability": "present",
                    }
                ],
            }
        ]
    )

    plan = apply_import(manifest, project)
    records = {record.id: record for record in list_records(config=project)}

    assert plan.applied is True
    dataset = records[plan.creates[-1]]
    assert dataset.type == "dataset"
    assert dataset.parent_id is not None
    assert records[dataset.parent_id].type == "collection"


def test_cli_preview_and_apply(tmp_path: Path) -> None:
    HirakuConfig.init(tmp_path, install_skill=False)
    manifest = _manifest()
    manifest.content_sha256 = manifest.compute_digest()
    path = tmp_path / "manifest.yml"
    _write_manifest(path, manifest)

    runner = CliRunner()
    preview = runner.invoke(main, ["registry", "import", str(path), "--json"])
    assert preview.exit_code == 0, preview.output
    assert '"applied": false' in preview.output

    applied = runner.invoke(
        main,
        [
            "registry",
            "import",
            str(path),
            "--apply",
            "--reviewed-hash",
            manifest.content_sha256,
            "--json",
        ],
    )
    assert applied.exit_code == 0, applied.output
    assert '"applied": true' in applied.output


def test_cli_tampered_hash_blocks_apply(tmp_path: Path) -> None:
    HirakuConfig.init(tmp_path, install_skill=False)
    manifest = _manifest()
    manifest.content_sha256 = "0" * 40
    path = tmp_path / "manifest.yml"
    _write_manifest(path, manifest)

    result = CliRunner().invoke(
        main,
        ["registry", "import", str(path), "--apply", "--reviewed-hash", "0" * 40],
    )

    assert result.exit_code != 0
    assert "content_sha256 does not match" in result.output


def test_cli_apply_requires_reviewed_hash(tmp_path: Path) -> None:
    config = HirakuConfig.init(tmp_path, install_skill=False)
    path = tmp_path / "manifest.yml"
    _write_manifest(path, _manifest())
    runner = CliRunner()

    result = runner.invoke(main, ["registry", "import", str(path), "--apply"])

    assert result.exit_code != 0
    assert "--apply requires --reviewed-hash" in result.output
    assert list_records(config=config) == []

    preview = runner.invoke(main, ["registry", "import", str(path)])
    assert f"content_sha256: {_manifest().compute_digest()}" in preview.output


def test_manifest_target_binds_the_project(project: HirakuConfig) -> None:
    other = _manifest(target="another-project")
    own = _manifest(target=project.root.name)

    with pytest.raises(ValidationError, match="targets project"):
        plan_import(other, project)
    with pytest.raises(ValidationError, match="targets project"):
        apply_import(other, project, reviewed_hash=other.compute_digest())
    assert list_records(config=project) == []
    assert other.compute_digest() != own.compute_digest()
    assert apply_import(own, project).applied is True


def test_history_keeps_the_replaced_observation_time(project: HirakuConfig) -> None:
    first = _manifest()
    first.datasets[0].observed_at = datetime(2026, 1, 1, tzinfo=UTC)
    apply_import(first, project)
    changed = _manifest()
    changed.datasets[0].artifacts[0].size = 43

    apply_import(changed, project)

    registration = list_records(config=project)[0].to_dict()["registration"]
    assert registration["history"][0]["observed_at"] == "2026-01-01T00:00:00Z"


def test_cli_invalid_manifest_reports_error(tmp_path: Path) -> None:
    HirakuConfig.init(tmp_path, install_skill=False)
    path = tmp_path / "manifest.yml"
    path.write_text("datasets: [{namespace: n, key: k}]\n", encoding="utf-8")

    result = CliRunner().invoke(main, ["registry", "import", str(path)])

    assert result.exit_code != 0
