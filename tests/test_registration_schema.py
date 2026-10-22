"""Schema, identity, and compatibility-mapping tests for dataset registration."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from hiraku.errors import ValidationError
from hiraku.registration import (
    Artifact,
    Dataset,
    Registration,
    RegistrationManifest,
    dataset_record_id,
    digest,
    normalize_display_path,
    redact_access,
)
from hiraku.registration.compat import (
    from_legacy_record,
    is_legacy_dataset,
    is_legacy_inventory,
)


def _manifest(
    datasets: list[Dataset], *, source: str = "example"
) -> RegistrationManifest:
    return RegistrationManifest(source=source, datasets=datasets)


def test_standalone_local_file_dataset() -> None:
    manifest = _manifest(
        [
            Dataset(
                namespace="filesystem",
                key=["data"],
                coverage="exhaustive",
                artifacts=[
                    Artifact(
                        key="data/raw.csv",
                        display_path="data/raw.csv",
                        revision="123:456",
                        size=42,
                        availability="present",
                        observed_at=datetime(2026, 10, 22, tzinfo=UTC),
                    )
                ],
            )
        ]
    )

    registration = Registration.model_validate(
        manifest.datasets[0].model_dump(mode="json")
    )
    assert registration.record_id() == dataset_record_id("filesystem", ["data"])
    assert registration.artifacts[0].size == 42
    assert manifest.compute_digest()


def test_descriptor_only_remote_data_without_size_or_revision() -> None:
    manifest = _manifest(
        [
            Dataset(
                namespace="remote",
                key="s3-bucket/set-a",
                coverage="unknown",
                artifacts=[
                    Artifact(
                        key="set-a/scan.tiff",
                        display_path="scans/scan.tiff",
                        access={"uri": "https://example.invalid/scans/scan.tiff"},
                    )
                ],
            )
        ]
    )

    assert manifest.datasets[0].artifacts[0].revision is None
    assert manifest.datasets[0].artifacts[0].size is None
    assert manifest.compute_digest()


def test_two_sources_with_overlapping_keys_do_not_collide() -> None:
    manifest = _manifest(
        [
            Dataset(namespace="source-a", key=["raw"], artifacts=[]),
            Dataset(namespace="source-b", key=["raw"], artifacts=[]),
        ]
    )

    ids = [dataset.record_id() for dataset in manifest.datasets]
    assert ids[0] != ids[1]


def test_duplicate_identity_is_rejected() -> None:
    with pytest.raises(ValidationError, match="duplicate dataset identities"):
        RegistrationManifest.model_validate(
            {
                "source": "source-a",
                "datasets": [
                    {"namespace": "source-a", "key": ["raw"]},
                    {"namespace": "source-a", "key": ["raw"]},
                ],
            }
        )


def test_no_technology_sample_or_sop_fields_are_mandatory() -> None:
    manifest = _manifest(
        [Dataset(namespace="plain", key="single", artifacts=[])]
    )

    assert manifest.datasets[0].metadata == {}
    assert Registration.model_validate(
        manifest.datasets[0].model_dump(mode="json")
    ).record_id() == dataset_record_id("plain", "single")


def test_unknown_revision_and_size_stay_null() -> None:
    artifact = Artifact(key="k", display_path="a/b.bin", availability="unknown")

    assert artifact.revision is None
    assert artifact.size is None


def test_non_exhaustive_coverage_cannot_mark_absence() -> None:
    with pytest.raises(ValidationError, match="exhaustive"):
        Dataset(
            namespace="n",
            key="k",
            coverage="partial",
            artifacts=[
                Artifact(key="a", display_path="a", availability="missing")
            ],
        )
    with pytest.raises(ValidationError, match="exhaustive"):
        Dataset(namespace="n", key="k", coverage="partial", availability="stale")


def test_exhaustive_coverage_allows_missing_and_stale() -> None:
    dataset = Dataset(
        namespace="n",
        key="k",
        coverage="exhaustive",
        artifacts=[
            Artifact(key="gone", display_path="gone.bin", availability="missing"),
            Artifact(key="old", display_path="old.bin", availability="stale"),
        ],
    )

    assert dataset.artifacts[0].availability == "missing"
    assert dataset.artifacts[1].availability == "stale"


def test_path_normalization_matches_jobs_paths() -> None:
    assert normalize_display_path("data/digits.npz") == "data/digits.npz"

    for invalid in ("/abs/path", "../up", "a\\b", ".", "a//b"):
        with pytest.raises(ValidationError):
            normalize_display_path(invalid)


def test_digests_are_stable_and_observation_time_is_not_semantic() -> None:
    first = Dataset(
        namespace="n",
        key="k",
        artifacts=[Artifact(key="a", display_path="a")],
        observed_at=datetime(2026, 1, 1, tzinfo=UTC),
    )
    second = Dataset(
        namespace="n",
        key="k",
        artifacts=[Artifact(key="a", display_path="a")],
        observed_at=datetime(2026, 10, 22, tzinfo=UTC),
    )

    first_reg = Registration.model_validate(first.model_dump(mode="json"))
    second_reg = Registration.model_validate(second.model_dump(mode="json"))
    assert first_reg.semantic_digest() == second_reg.semantic_digest()


def test_changed_artifact_changes_digest() -> None:
    first = Registration(
        namespace="n", key="k", artifacts=[Artifact(key="a", display_path="a")]
    )
    second = Registration(
        namespace="n",
        key="k",
        artifacts=[Artifact(key="a", display_path="a", size=1)],
    )

    assert first.semantic_digest() != second.semantic_digest()


def test_digest_is_deterministic() -> None:
    assert digest([1, 2]) == digest([1, 2])
    assert digest({"a": 1, "b": 2}) == digest({"b": 2, "a": 1})
    assert digest([1, 2]) != digest([2, 1])


def test_redact_access_strips_credentials() -> None:
    assert redact_access({"uri": "https://host/x", "token": "secret"}) == {
        "uri": "https://host/x",
        "token": "<redacted>",
    }
    assert redact_access({"nested": {"password": "x"}}) == {
        "nested": {"password": "<redacted>"}
    }
    assert redact_access(None) is None


def test_manifest_rejects_unknown_schema_version() -> None:
    with pytest.raises(ValidationError, match="schema_version"):
        RegistrationManifest.model_validate({"schema_version": 2, "source": "s"})


def test_manifest_allows_dataset_namespace_distinct_from_source() -> None:
    # The Flagship cohort adapter uses the cohort ID as the identity namespace
    # while its adapter name is the manifest source.
    manifest = RegistrationManifest.model_validate(
        {"source": "flagship-cohort", "datasets": [{"namespace": "CSCS", "key": "s"}]}
    )

    assert manifest.datasets[0].record_id() == dataset_record_id("CSCS", "s")


def test_registration_rejects_unknown_schema() -> None:
    with pytest.raises(ValidationError, match="schema"):
        Registration.model_validate(
            {"schema": 2, "namespace": "n", "key": "k"}
        )


def test_dataset_key_accepts_string_and_list() -> None:
    assert Dataset(namespace="n", key="k").key == "k"
    assert Dataset(namespace="n", key=["a", "b"]).key == ["a", "b"]
    with pytest.raises(ValidationError):
        Dataset(namespace="n", key=["a", ""])


# --- Compatibility mapping ---------------------------------------------------


LEGACY_DATASET = {
    "id": "dataset-0123456789abcdef0123",
    "parent_id": "technology-abc",
    "related_to": ["sop-abc", "sop-def"],
    "type": "dataset",
    "status": "registered",
    "technology": "scrnaseq",
    "registry": {
        "schema_version": 1,
        "cohort_id": "CSCS",
        "scan_complete": True,
        "sop_catalog_complete": True,
        "artifact_count": 2,
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
            "matches": [
                {
                    "sop_id": "sop-abc",
                    "expected_filename": "/mount/counts.csv",
                    "source_excerpt": "write counts",
                }
            ],
        },
        {
            "artifact_id": "art-2",
            "path": "derived/pass_1/notes.txt",
            "state": "file",
            "size_bytes": 9,
            "processing_pass": "pass_1",
            "revision": "9:10",
            "documented": False,
            "matches": [],
        },
    ],
    "missing_expected": [
        {
            "sop_id": "sop-def",
            "expected_filename": "/mount/expected.txt",
            "availability": "not_found",
        }
    ],
    "markdown": "Observed cohort artifact inventory imported from descriptors.",
}


def test_legacy_dataset_is_recognized() -> None:
    assert is_legacy_dataset(LEGACY_DATASET)
    assert not is_legacy_inventory(LEGACY_DATASET)


def test_legacy_dataset_maps_to_registration() -> None:
    registration = from_legacy_record(LEGACY_DATASET)

    assert registration is not None
    assert registration.namespace == "CSCS"
    assert registration.key == ["sample-1", "scrnaseq"]
    assert registration.coverage == "exhaustive"
    assert registration.metadata["technology"] == "scrnaseq"
    assert [artifact.key for artifact in registration.artifacts] == [
        "art-1",
        "art-2",
        "missing-sop-def-/mount/expected.txt",
    ]
    present = next(a for a in registration.artifacts if a.key == "art-1")
    assert present.display_path == "derived/pass_1/counts.csv"
    assert present.size == 100
    assert present.availability == "present"
    assert [link.key for link in present.documentation] == ["sop-abc"]
    missing = next(
        a for a in registration.artifacts if a.key.startswith("missing-sop-def")
    )
    assert missing.availability == "missing"
    assert {link.key for link in registration.documentation} == {"sop-abc", "sop-def"}


def test_legacy_inventory_maps_to_registration() -> None:
    legacy = {
        "inventory_files": [
            {"path": "data/a.csv", "state": "file", "size_bytes": 3, "revision": "3:1"},
            {"path": "data/b.csv", "state": "file", "size_bytes": 4, "revision": "4:1"},
        ],
        "file_inventory": {"roots": ["data"], "scan_complete": True, "file_count": 2},
    }

    registration = from_legacy_record(legacy)

    assert registration is not None
    assert registration.namespace == "filesystem"
    assert registration.coverage == "exhaustive"
    assert [a.display_path for a in registration.artifacts] == [
        "data/a.csv",
        "data/b.csv",
    ]


def test_legacy_inventory_is_recognized() -> None:
    assert is_legacy_inventory({"inventory_files": []})
    assert not is_legacy_dataset({"inventory_files": []})


def test_unrelated_record_maps_to_none() -> None:
    assert from_legacy_record({"id": "x", "type": "experiment"}) is None


def test_malformed_legacy_dataset_raises() -> None:
    with pytest.raises(ValidationError):
        from_legacy_record(
            {
                "type": "dataset",
                "registry": {"cohort_id": "CSCS"},
                "files": [{"artifact_id": "a"}],  # missing path
            }
        )
    with pytest.raises(ValidationError):
        from_legacy_record({"type": "dataset", "registry": {}, "files": []})


def test_record_id_is_stable_across_sources() -> None:
    assert dataset_record_id("n", "k") == dataset_record_id("n", "k")
    assert dataset_record_id("n", ["a", "b"]) == dataset_record_id("n", ["a", "b"])


def test_registered_record_is_readable_by_mains_hiraku(tmp_path, monkeypatch) -> None:
    from pathlib import Path

    from click.testing import CliRunner

    from hiraku.cli import main
    from hiraku.config import HirakuConfig
    from hiraku.record import Record, list_records

    config = HirakuConfig.init(Path(tmp_path), install_skill=False)
    registration = Registration(
        namespace="filesystem",
        key=["data"],
        coverage="exhaustive",
        artifacts=[
            Artifact(
                key="data/raw.csv",
                display_path="data/raw.csv",
                revision="123:456",
                size=42,
                availability="present",
            )
        ],
    )
    record = Record.create(
        registration.record_id(),
        type="dataset",
        registration=registration.to_record_value(),
        activate=False,
        config=config,
    )

    stored = list_records(config=config)[0]
    assert stored.id == record.id
    assert stored.type == "dataset"
    assert Registration.from_record_value(stored.to_dict()["registration"]).artifacts[
        0
    ].display_path == "data/raw.csv"

    monkeypatch.chdir(tmp_path)
    result = CliRunner().invoke(main, ["record", "show", record.id, "--json"])
    assert result.exit_code == 0, result.output
    assert "registration" in result.output
    assert "data/raw.csv" in result.output
