"""Backward-compatible mapping of legacy record fields to the registry schema.

Before the generic registration package, two writers stored registry data as
ordinary record extras. This module maps those shapes to
:class:`~hiraku.registration.schema.Registration` without inventing fields:

* the Flagship cohort adapter wrote ``type: dataset`` records with ``registry``,
  ``files``, ``missing_expected``, ``related_to``, and ``importer_fields``;
* an earlier ``hiraku files scan`` wrote ``inventory_files`` and
  ``file_inventory`` onto a user-chosen record.

The mapping is explicit and audited: legacy shapes are validated, not silently
accepted as arbitrary dictionaries. Deterministic record IDs are preserved on
migration (the writer matches datasets by ``(namespace, key)`` identity rather
than by recomputing an ID), so reimporting legacy cohort records never
duplicates a dataset or drops human text.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from ..errors import ValidationError
from .schema import (
    Artifact,
    Coverage,
    Documentation,
    Registration,
    normalize_display_path,
)

_FILESYSTEM_NAMESPACE = "filesystem"


def is_legacy_dataset(fields: Mapping[str, Any]) -> bool:
    """Return whether ``fields`` look like a Flagship writer dataset record."""
    return (
        fields.get("type") == "dataset"
        and isinstance(fields.get("registry"), Mapping)
        and ("files" in fields or "missing_expected" in fields)
    )


def is_legacy_inventory(fields: Mapping[str, Any]) -> bool:
    """Return whether ``fields`` look like a ``hiraku files scan`` inventory."""
    return isinstance(fields.get("inventory_files"), list) or isinstance(
        fields.get("file_inventory"), Mapping
    )


def from_legacy_record(fields: Mapping[str, Any]) -> Registration | None:
    """Map a legacy dataset or inventory record to a ``Registration``.

    Returns ``None`` when the record carries no recognized registry data. Raises
    :class:`~hiraku.errors.ValidationError` for a recognized but malformed shape.
    """
    if is_legacy_dataset(fields):
        return _from_legacy_dataset(fields)
    if is_legacy_inventory(fields):
        return _from_legacy_inventory(fields)
    return None


def _from_legacy_dataset(fields: Mapping[str, Any]) -> Registration:
    registry = _mapping(fields["registry"], "registry")
    cohort_id = registry.get("cohort_id")
    if not isinstance(cohort_id, str) or not cohort_id:
        raise ValidationError("legacy dataset registry.cohort_id is required")
    identity_inputs = registry.get("identity_inputs")
    key = _identity_key(identity_inputs, cohort_id, registry)
    coverage: Coverage = (
        "exhaustive" if registry.get("scan_complete") is True else "partial"
    )
    metadata: dict[str, Any] = {}
    if isinstance(fields.get("technology"), str):
        metadata["technology"] = fields["technology"]
    artifacts = [_artifact(row) for row in _list(fields.get("files"), "files")]
    for missing in _list(fields.get("missing_expected"), "missing_expected"):
        artifacts.append(_missing_artifact(missing))
    documentation = _sop_links(
        _list(fields.get("related_to"), "related_to"),
        artifacts,
        registry.get("sop_catalog_complete"),
    )
    return Registration(
        schema=1,
        namespace=cohort_id,
        key=key,
        metadata=metadata,
        coverage=coverage,
        artifacts=artifacts,
        documentation=documentation,
    )


def _identity_key(
    identity_inputs: Any, cohort_id: str, registry: Mapping[str, Any]
) -> str | list[str]:
    if not isinstance(identity_inputs, list) or not identity_inputs:
        raise ValidationError("legacy dataset registry.identity_inputs is required")
    if not all(isinstance(item, str) and item for item in identity_inputs):
        raise ValidationError(
            "legacy dataset registry.identity_inputs must list non-empty strings"
        )
    # The legacy digest was built from [cohort_id, *source key]; the cohort is
    # the namespace, so the source key is the tail.
    if identity_inputs[0] == cohort_id:
        return identity_inputs[1:]
    return identity_inputs


def _artifact(row: Any) -> Artifact:
    item = _mapping(row, "files entry")
    artifact_id = item.get("artifact_id")
    if not isinstance(artifact_id, str) or not artifact_id:
        raise ValidationError("legacy file entry requires an artifact_id")
    path = item.get("path")
    if not isinstance(path, str) or not path:
        raise ValidationError("legacy file entry requires a path")
    size = item.get("size_bytes")
    documentation = [
        _match(match) for match in _list(item.get("matches"), "files entry matches")
    ]
    artifact = Artifact(
        key=artifact_id,
        display_path=normalize_display_path(path, label="legacy file path"),
        revision=_optional_str(item.get("revision"), "revision"),
        size=_optional_size(size),
        availability="present" if item.get("state") == "file" else "unknown",
        documentation=documentation,
    )
    if "processing_pass" in item:
        artifact.processing_pass = item["processing_pass"]
    return artifact


def _missing_artifact(row: Any) -> Artifact:
    item = _mapping(row, "missing_expected entry")
    sop_id = item.get("sop_id")
    filename = item.get("expected_filename")
    if not isinstance(sop_id, str) or not sop_id:
        raise ValidationError("legacy missing_expected entry requires a sop_id")
    if not isinstance(filename, str) or not filename:
        raise ValidationError(
            "legacy missing_expected entry requires expected_filename"
        )
    availability = item.get("availability")
    observed = "missing" if availability == "not_found" else "unknown"
    return Artifact(
        key=f"missing-{sop_id}-{filename}",
        display_path=normalize_display_path(
            _basename(filename), label="expected_filename"
        ),
        availability=observed,  # type: ignore[arg-type]
        documentation=[
            Documentation(
                key=sop_id,
                role="unknown",
                applicability="unknown",
                filename=filename,
            )
        ],
    )


def _match(match: Any) -> Documentation:
    item = _mapping(match, "match")
    sop_id = item.get("sop_id")
    if not isinstance(sop_id, str) or not sop_id:
        raise ValidationError("legacy match requires a sop_id")
    # The legacy matcher used the bounded SOP scan, which does not establish
    # whether a reference is an input, an output, or a script.
    return Documentation(
        key=sop_id,
        role="unknown",
        applicability="unknown",
        filename=_optional_str(item.get("expected_filename"), "expected_filename"),
    )


def _sop_links(
    related: list[Any], artifacts: list[Artifact], sop_complete: Any
) -> list[Documentation]:
    seen: dict[str, Documentation] = {}
    for artifact in artifacts:
        for link in artifact.documentation:
            seen.setdefault(link.key, link)
    # Legacy ``related_to`` links SOP records but carries no role information,
    # so role and applicability stay unknown.
    for identifier in related:
        if isinstance(identifier, str) and identifier:
            seen.setdefault(identifier, Documentation(key=identifier))
    return list(seen.values())


def _from_legacy_inventory(fields: Mapping[str, Any]) -> Registration:
    inventory = _mapping(fields.get("file_inventory"), "file_inventory")
    coverage: Coverage = (
        "exhaustive" if inventory.get("scan_complete") is True else "partial"
    )
    roots = _list(inventory.get("roots"), "file_inventory roots")
    entries = _list(fields.get("inventory_files"), "inventory_files")
    artifacts = [
        Artifact(
            key=_inventory_artifact_key(entry, index),
            display_path=normalize_display_path(
                entry.get("path"), label="inventory_files path"
            ),
            revision=_optional_str(entry.get("revision"), "revision"),
            size=_optional_size(entry.get("size_bytes")),
            availability="present" if entry.get("state") == "file" else "unknown",
        )
        for index, entry in enumerate(entries)
    ]
    key: str | list[str] = [str(root) for root in roots] or "inventory"
    return Registration(
        schema=1,
        namespace=_FILESYSTEM_NAMESPACE,
        key=key,
        metadata={"roots": roots},
        coverage=coverage,
        artifacts=artifacts,
    )


def _inventory_artifact_key(entry: Mapping[str, Any], index: int) -> str:
    artifact_id = entry.get("artifact_id")
    if isinstance(artifact_id, str) and artifact_id:
        return artifact_id
    path = entry.get("path")
    if isinstance(path, str) and path:
        return f"file-{index}-{path}"
    raise ValidationError("legacy inventory_files entry requires a path")


def _mapping(value: Any, label: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise ValidationError(f"legacy {label} must be a mapping")
    return value


def _list(value: Any, label: str) -> list[Any]:
    if value is None:
        return []
    if not isinstance(value, list):
        raise ValidationError(f"legacy {label} must be a list")
    return value


def _optional_str(value: Any, label: str) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str):
        raise ValidationError(f"legacy {label} must be a string or null")
    return value


def _optional_size(value: Any) -> int | None:
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise ValidationError(
            "legacy size_bytes must be a non-negative integer or null"
        )
    return value


def _basename(path: str) -> str:
    from pathlib import PurePosixPath

    return PurePosixPath(path).name
