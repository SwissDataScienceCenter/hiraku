"""Field ownership, refresh lifecycle, and retry tests for registration."""

from __future__ import annotations

from pathlib import Path

import pytest

from hiraku.config import HirakuConfig
from hiraku.errors import ValidationError
from hiraku.record import Record, list_records, load_record
from hiraku.registration import (
    Dataset,
    Registration,
    RegistrationManifest,
    apply_import,
    plan_import,
)
from hiraku.registration.compat import from_legacy_record
from hiraku.registration.writer import ApplyResult, apply_writes


@pytest.fixture
def project(tmp_path: Path) -> HirakuConfig:
    return HirakuConfig.init(tmp_path, install_skill=False)


def _dataset(key: str, artifacts: list[dict], **overrides) -> Dataset:
    data = {
        "namespace": "filesystem",
        "key": key,
        "coverage": "exhaustive",
        "roots": ["data"],
        "artifacts": artifacts,
    }
    data.update(overrides)
    return Dataset.model_validate(data)


def _manifest(source: str, *datasets: Dataset) -> RegistrationManifest:
    return RegistrationManifest(source=source, datasets=list(datasets))


def _artifact(
    key: str, availability: str = "present", revision: str | None = "1:1"
) -> dict:
    return {
        "key": key,
        "display_path": key,
        "availability": availability,
        "revision": revision,
        "size": 1,
    }


def test_identical_apply_is_a_no_op(project: HirakuConfig) -> None:
    manifest = _manifest("filesystem", _dataset("data", [_artifact("data/a.csv")]))
    apply_import(manifest, project)
    path = list_records(config=project)[0].path

    before = path.read_text(encoding="utf-8")
    again = apply_import(manifest, project)
    after = path.read_text(encoding="utf-8")

    assert again.unchanged == 1
    assert again.creates == [] and again.updates == []
    assert after == before


def test_human_edits_and_links_survive(project: HirakuConfig) -> None:
    manifest = _manifest("filesystem", _dataset("data", [_artifact("data/a.csv")]))
    record_id = manifest.datasets[0].record_id()
    apply_import(manifest, project)
    Record.create("notes", type="note", config=project)

    record = load_record(record_id, config=project)
    record.status = "reviewed"
    record.note = "human annotation"
    record.related_to = ["notes"]
    record.save()

    plan = apply_import(manifest, project)

    assert plan.unchanged == 1
    stored = load_record(record_id, config=project)
    assert stored.status == "reviewed"
    assert stored.note == "human annotation"
    assert stored.related_to == ["notes"]


def test_human_edit_to_owned_field_conflicts(project: HirakuConfig) -> None:
    manifest = _manifest("filesystem", _dataset("data", [_artifact("data/a.csv")]))
    record_id = manifest.datasets[0].record_id()
    apply_import(manifest, project)

    record = load_record(record_id, config=project)
    record.type = "experiment"
    record.save()

    plan = plan_import(manifest, project)

    assert plan.conflicts
    assert "type" in plan.conflicts[0].message


def test_override_conflicts_rewrites_owned_field(project: HirakuConfig) -> None:
    manifest = _manifest("filesystem", _dataset("data", [_artifact("data/a.csv")]))
    record_id = manifest.datasets[0].record_id()
    apply_import(manifest, project)

    record = load_record(record_id, config=project)
    record.type = "experiment"
    record.save()

    plan = apply_import(manifest, project, override=True)

    assert plan.applied is True
    assert load_record(record_id, config=project).type == "dataset"


def test_source_ownership_conflict_is_reported(project: HirakuConfig) -> None:
    manifest = _manifest("filesystem", _dataset("data", [_artifact("data/a.csv")]))
    apply_import(manifest, project)

    other = _manifest("other-source", _dataset("data", [_artifact("data/a.csv")]))

    plan = plan_import(other, project)

    assert plan.conflicts
    assert "owned by source" in plan.conflicts[0].message


def test_reordered_manifest_is_a_no_op(project: HirakuConfig) -> None:
    manifest = _manifest(
        "filesystem",
        _dataset("data", [_artifact("data/b.csv"), _artifact("data/a.csv")]),
    )
    apply_import(manifest, project)

    reordered = _manifest(
        "filesystem",
        _dataset("data", [_artifact("data/a.csv"), _artifact("data/b.csv")]),
    )
    plan = apply_import(reordered, project)

    assert plan.unchanged == 1
    assert plan.updates == []


def test_exhaustive_rescan_marks_disappeared_artifacts_stale(
    project: HirakuConfig,
) -> None:
    manifest = _manifest(
        "filesystem",
        _dataset("data", [_artifact("data/a.csv"), _artifact("data/b.csv")]),
    )
    record_id = manifest.datasets[0].record_id()
    apply_import(manifest, project)

    narrowed = _manifest("filesystem", _dataset("data", [_artifact("data/a.csv")]))
    apply_import(narrowed, project)

    stored = Registration.from_record_value(
        load_record(record_id, config=project).to_dict()["registration"]
    )
    by_key = {artifact.key: artifact for artifact in stored.artifacts}
    assert by_key["data/a.csv"].availability == "present"
    assert by_key["data/b.csv"].availability == "stale"
    assert stored.history


def test_partial_scan_merges_without_claiming_absence(project: HirakuConfig) -> None:
    manifest = _manifest(
        "filesystem",
        _dataset("data", [_artifact("data/a.csv"), _artifact("data/b.csv")]),
    )
    record_id = manifest.datasets[0].record_id()
    apply_import(manifest, project)

    partial = _manifest(
        "filesystem",
        _dataset(
            "data",
            [_artifact("data/a.csv")],
            coverage="partial",
        ),
    )
    apply_import(partial, project)

    stored = Registration.from_record_value(
        load_record(record_id, config=project).to_dict()["registration"]
    )
    assert {a.key: a.availability for a in stored.artifacts} == {
        "data/a.csv": "present",
        "data/b.csv": "present",
    }


def test_narrowed_scan_does_not_remove_artifacts(project: HirakuConfig) -> None:
    manifest = _manifest(
        "filesystem",
        _dataset(
            "data",
            [_artifact("data/a.csv"), _artifact("raw/b.csv")],
            roots=["data", "raw"],
        ),
    )
    record_id = manifest.datasets[0].record_id()
    apply_import(manifest, project)

    narrowed = _manifest(
        "filesystem",
        _dataset("data", [_artifact("data/a.csv")], roots=["data"]),
    )
    apply_import(narrowed, project)

    stored = Registration.from_record_value(
        load_record(record_id, config=project).to_dict()["registration"]
    )
    assert {a.key: a.availability for a in stored.artifacts} == {
        "data/a.csv": "present",
        "raw/b.csv": "present",
    }


def test_revision_change_updates_and_records_history(project: HirakuConfig) -> None:
    manifest = _manifest("filesystem", _dataset("data", [_artifact("data/a.csv")]))
    record_id = manifest.datasets[0].record_id()
    apply_import(manifest, project)

    changed = _manifest(
        "filesystem",
        _dataset("data", [_artifact("data/a.csv", revision="2:2")]),
    )
    apply_import(changed, project)

    stored = Registration.from_record_value(
        load_record(record_id, config=project).to_dict()["registration"]
    )
    assert stored.artifacts[0].revision == "2:2"
    assert len(stored.history) == 1


def test_mid_batch_failure_is_audited_and_retryable(
    project: HirakuConfig, monkeypatch: pytest.MonkeyPatch
) -> None:
    import hiraku.registration.writer as writer_module

    manifest = _manifest(
        "filesystem",
        _dataset("set-a", [_artifact("a/1.csv")]),
        _dataset("set-b", [_artifact("b/2.csv")]),
    )
    real_create = writer_module.Record.create
    calls = {"count": 0}

    def flaky_create(record_id, **kwargs):
        calls["count"] += 1
        if calls["count"] == 2:
            raise OSError("simulated disk failure")
        return real_create(record_id, **kwargs)

    monkeypatch.setattr(writer_module.Record, "create", staticmethod(flaky_create))

    with pytest.raises(ValidationError, match="retry is safe"):
        apply_import(manifest, project)

    # The first dataset was created before the failure.
    assert manifest.datasets[0].record_id() in {
        record.id for record in list_records(config=project)
    }

    monkeypatch.undo()
    retry = apply_import(manifest, project)
    assert retry.applied is True
    assert {record.id for record in list_records(config=project)} == {
        dataset.record_id() for dataset in manifest.datasets
    }


def test_legacy_cohort_records_reimport_without_duplication(
    project: HirakuConfig,
) -> None:
    legacy = {
        "id": "dataset-0123456789abcdef0123",
        "parent_id": "technology-abc",
        "type": "dataset",
        "status": "registered",
        "technology": "scrnaseq",
        "registry": {
            "schema_version": 1,
            "cohort_id": "CSCS",
            "scan_complete": True,
            "sop_catalog_complete": True,
            "artifact_count": 1,
            "matched_artifact_count": 1,
            "source_config": "application.toml",
            "identity_inputs": ["CSCS", "sample-1", "scrnaseq"],
        },
        "files": [
            {
                "artifact_id": "art-1",
                "path": "derived/pass_1/counts.csv",
                "state": "file",
                "size_bytes": 100,
                "processing_pass": "pass_1",
                "revision": "100:200",
                "documented": True,
                "matches": [],
            }
        ],
        "missing_expected": [],
        "markdown": "Observed cohort artifact inventory imported from descriptors.",
    }
    Record.create(
        legacy["id"],
        type="dataset",
        status="registered",
        technology="scrnaseq",
        registry=legacy["registry"],
        files=legacy["files"],
        missing_expected=[],
        markdown=legacy["markdown"],
        parent_id=Record.create("technology-abc", type="technology", config=project).id,
        config=project,
    )

    registration = from_legacy_record(legacy)
    assert registration is not None
    dataset = Dataset(
        namespace=registration.namespace,
        key=registration.key,
        parent=registration.parent,
        metadata=registration.metadata,
        coverage=registration.coverage,
        roots=registration.roots,
        artifacts=registration.artifacts,
        documentation=registration.documentation,
    )
    manifest = _manifest("flagship-cohort", dataset)

    first = plan_import(manifest, project)
    assert first.creates == []
    assert first.updates == [legacy["id"]]

    apply_import(manifest, project)

    records = list_records(config=project)
    dataset_ids = [r.id for r in records if r.type == "dataset"]
    assert dataset_ids == [legacy["id"]]
    assert load_record(legacy["id"], config=project).markdown == legacy["markdown"]

    # A second, identical reimport is a no-op: no duplicate, no further edits.
    second = apply_import(manifest, project)
    assert second.unchanged == 1
    assert second.updates == []


def test_apply_writes_returns_audit_summary(project: HirakuConfig) -> None:
    from hiraku.registration.review import _registration_for, _Write

    manifest = _manifest("filesystem", _dataset("data", [_artifact("data/a.csv")]))
    dataset = manifest.datasets[0]
    writes = [
        _Write(
            record_id=dataset.record_id(),
            record_type="dataset",
            registration=_registration_for(dataset, source=manifest.source),
            create=True,
        )
    ]

    result = apply_writes(project, writes)

    assert isinstance(result, ApplyResult)
    assert result.applied == [dataset.record_id()]
    assert result.failed == []
