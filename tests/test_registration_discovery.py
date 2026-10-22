"""Filesystem discovery and `hiraku registry scan` tests."""

from __future__ import annotations

import os
from pathlib import Path

import pytest
from click.testing import CliRunner

from hiraku.cli import main
from hiraku.config import HirakuConfig
from hiraku.errors import ValidationError
from hiraku.io import dump_yaml
from hiraku.record import list_records
from hiraku.registration import apply_import, scan_files, scan_manifest
from hiraku.registration.discovery import external_roots


@pytest.fixture
def project(tmp_path: Path) -> HirakuConfig:
    config = HirakuConfig.init(tmp_path, install_skill=False)
    (tmp_path / "data").mkdir()
    (tmp_path / "data" / "a.csv").write_text("x", encoding="utf-8")
    (tmp_path / "data" / "sub").mkdir()
    (tmp_path / "data" / "sub" / "b.csv").write_text("y", encoding="utf-8")
    return config


def test_project_relative_scan_produces_jobs_style_paths(project: HirakuConfig) -> None:
    manifest = scan_manifest(project, [Path("data")])

    artifacts = manifest.datasets[0].artifacts
    assert {a.display_path for a in artifacts} == {"data/a.csv", "data/sub/b.csv"}
    assert all(a.root is None for a in artifacts)
    assert all(a.revision.startswith("1:") for a in artifacts)


def test_scan_does_not_read_contents(project: HirakuConfig) -> None:
    (project.root / "data" / "big.bin").write_bytes(b"\x00" * 1024)

    artifacts = scan_files(project, [Path("data")])

    big = next(a for a in artifacts if a.display_path == "data/big.bin")
    assert big.size == 1024
    assert big.revision == f"{1024}:{big.revision.split(':', 1)[1]}"


def test_external_root_requires_configuration(project: HirakuConfig) -> None:
    with pytest.raises(ValidationError, match="root is outside the project"):
        scan_files(project, [Path("/etc")])


def test_configured_external_roots_work(project: HirakuConfig) -> None:
    mount = project.root / ".." / "external-mount"
    mount.mkdir()
    (mount / "scan.tiff").write_text("z", encoding="utf-8")
    _set_dataset_roots(project, {"cohort": str(mount.resolve())})

    assert external_roots(HirakuConfig.load(project.path)) == {
        "cohort": str(mount.resolve())
    }
    manifest = scan_manifest(project, [], {"cohort": str(mount.resolve())})

    artifact = manifest.datasets[0].artifacts[0]
    assert artifact.root == "cohort"
    assert artifact.display_path == "scan.tiff"
    assert artifact.key == "cohort/scan.tiff"


def test_identity_survives_mount_relocation(project: HirakuConfig) -> None:
    first = project.root / ".." / "mount-a"
    first.mkdir()
    (first / "f.bin").write_text("z", encoding="utf-8")
    second = project.root / ".." / "mount-b"
    second.mkdir()
    (second / "f.bin").write_text("z", encoding="utf-8")

    before = scan_manifest(project, [], {"cohort": str(first.resolve())})
    after = scan_manifest(project, [], {"cohort": str(second.resolve())})

    assert before.datasets[0].record_id() == after.datasets[0].record_id()
    assert before.datasets[0].artifacts[0].key == after.datasets[0].artifacts[0].key


def test_symlinks_are_skipped(project: HirakuConfig) -> None:
    os.symlink(project.root / "data" / "a.csv", project.root / "data" / "link.csv")

    artifacts = scan_files(project, [Path("data")])

    assert "data/link.csv" not in {a.display_path for a in artifacts}


def test_overlapping_roots_are_deduplicated(project: HirakuConfig) -> None:
    artifacts = scan_files(project, [Path("data"), Path("data")])

    display_paths = [a.display_path for a in artifacts]
    assert len(display_paths) == len(set(display_paths))


def test_directories_are_not_artifacts(project: HirakuConfig) -> None:
    artifacts = scan_files(project, [Path("data")])

    assert all(a.display_path != "data/sub" for a in artifacts)


def test_exhaustive_scan_never_silently_truncates(project: HirakuConfig) -> None:
    with pytest.raises(ValidationError, match="max_files"):
        scan_files(project, [Path("data")], max_files=1)


def test_scan_requires_a_root(project: HirakuConfig) -> None:
    with pytest.raises(ValidationError, match="at least one root"):
        scan_files(project, [])


def test_failed_scan_leaves_prior_records_intact(project: HirakuConfig) -> None:
    manifest = scan_manifest(project, [Path("data")])
    apply_import(manifest, project)
    before = [record.to_dict() for record in list_records(config=project)]

    with pytest.raises(ValidationError):
        scan_files(project, [Path("missing")], max_files=1)

    after = [record.to_dict() for record in list_records(config=project)]
    assert after == before


def test_scan_manifest_feeds_the_same_import_path(project: HirakuConfig) -> None:
    manifest = scan_manifest(project, [Path("data")])

    plan = apply_import(manifest, project)

    assert plan.applied is True
    records = list_records(config=project)
    assert len(records) == 1
    assert records[0].type == "dataset"


def test_scan_cli_preview_and_output(
    project: HirakuConfig, tmp_path: Path
) -> None:
    runner = CliRunner()
    result = runner.invoke(
        main,
        [
            "registry",
            "scan",
            "--project",
            str(project.root),
            "--root",
            "data",
            "--json",
        ],
    )
    assert result.exit_code == 0, result.output
    assert '"applied": false' in result.output
    assert "dataset-" in result.output

    output = tmp_path / "manifest.yml"
    written = runner.invoke(
        main,
        [
            "registry",
            "scan",
            "--project",
            str(project.root),
            "--root",
            "data",
            "--output",
            str(output),
        ],
    )
    assert written.exit_code == 0, written.output
    assert output.is_file()
    assert "content_sha256" in output.read_text(encoding="utf-8")


def test_scan_cli_rejects_unconfigured_external_root(project: HirakuConfig) -> None:
    runner = CliRunner()
    result = runner.invoke(
        main,
        [
            "registry",
            "scan",
            "--project",
            str(project.root),
            "--root",
            "data",
            "--external",
            "unconfigured",
        ],
    )

    assert result.exit_code != 0
    assert "not configured" in result.output


def _set_dataset_roots(config: HirakuConfig, mapping: dict[str, str]) -> None:
    data = dump_yaml({**config.to_dict(), "dataset_roots": mapping})
    config.path.write_text(data, encoding="utf-8")
