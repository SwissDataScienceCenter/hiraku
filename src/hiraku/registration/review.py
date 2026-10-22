"""Manifest loading, validation, and import planning.

The review layer turns a source adapter's manifest into a concrete, reviewable
plan: which records will be created, updated, left unchanged, or conflict. It
never writes by itself. ``apply_import`` verifies the reviewed manifest digest
and delegates persistence to the writer.
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from ..config import HirakuConfig
from ..errors import ValidationError
from ..io import load_yaml_mapping
from ..record import Record, list_records
from .compat import from_legacy_record
from .schema import (
    Artifact,
    Dataset,
    Observation,
    Registration,
    RegistrationManifest,
    canonical_json,
    content_digest,
    digest,
)

_HISTORY_LIMIT = 20


@dataclass(frozen=True)
class Conflict:
    """A dataset that cannot be applied without an explicit decision."""

    record_id: str
    message: str


@dataclass
class _Write:
    """One ordered persistence step for the writer."""

    record_id: str
    record_type: str
    registration: Registration
    parent_id: str | None = None
    create: bool = True
    existing: Record | None = None


@dataclass
class ImportPlan:
    """The reviewed outcome of importing one manifest into a project."""

    project: Path
    source: str
    content_sha256: str | None = None
    creates: list[str] = field(default_factory=list)
    updates: list[str] = field(default_factory=list)
    unchanged: int = 0
    conflicts: list[Conflict] = field(default_factory=list)
    applied: bool = False
    _writes: list[_Write] = field(default_factory=list, repr=False)

    def summary(self) -> dict[str, Any]:
        """Return a JSON-serializable review summary."""
        return {
            "project": str(self.project),
            "source": self.source,
            "content_sha256": self.content_sha256,
            "creates": self.creates,
            "updates": self.updates,
            "unchanged": self.unchanged,
            "conflicts": [conflict.__dict__ for conflict in self.conflicts],
            "applied": self.applied,
        }


def _read_manifest(path: Path) -> dict[str, Any]:
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as exc:
        raise ValidationError(f"Cannot read registry manifest {path}: {exc}") from exc
    if path.suffix == ".json":
        try:
            value = json.loads(text)
        except ValueError as exc:
            raise ValidationError(f"Malformed registry manifest {path}: {exc}") from exc
        if not isinstance(value, Mapping):
            raise ValidationError(f"Registry manifest must be a mapping: {path}")
        return dict(value)
    return load_yaml_mapping(path)


def load_manifest(path: Path | str) -> RegistrationManifest:
    """Load and validate a registry manifest, checking its internal digest.

    The manifest's ``content_sha256``, when present, must match a recomputed
    digest of its semantic content, so a tampered manifest fails here.
    """
    data = _read_manifest(Path(path).expanduser())
    return validate_manifest(data, check_digest=True)


def validate_manifest(
    data: Mapping[str, Any] | RegistrationManifest, *, check_digest: bool = False
) -> RegistrationManifest:
    """Validate a manifest mapping or model and return it as a model.

    Raises:
        hiraku.errors.ValidationError: For malformed metadata, an unknown
            schema, duplicate identity, or contradictory completeness.
    """
    manifest = (
        data
        if isinstance(data, RegistrationManifest)
        else RegistrationManifest.model_validate(dict(data))
    )
    if (
        check_digest
        and manifest.content_sha256 is not None
        and manifest.content_sha256 != manifest.compute_digest()
    ):
        raise ValidationError("manifest content_sha256 does not match its content")
    return manifest


def verify_reviewed_hash(
    manifest: RegistrationManifest, reviewed_hash: str | None
) -> None:
    """Confirm that a reviewed hash binds this exact manifest.

    ``reviewed_hash`` must equal the manifest's declared ``content_sha256``, or
    the recomputed digest when no hash was declared.
    """
    if reviewed_hash is None:
        return
    expected = manifest.content_sha256 or manifest.compute_digest()
    if reviewed_hash != expected:
        raise ValidationError(
            "reviewed hash does not match the manifest; review the latest "
            "manifest and pass its content_sha256"
        )


def verify_target(manifest: RegistrationManifest, config: HirakuConfig) -> None:
    """Confirm that a manifest naming a ``target`` is applied to that project.

    ``target`` is the target project's directory name. It is part of the
    reviewed digest, so a reviewed manifest cannot be applied elsewhere.
    """
    if manifest.target is not None and manifest.target != config.root.name:
        raise ValidationError(
            f"manifest targets project {manifest.target!r}, not {config.root.name!r}"
        )


def _identity_of(record: Record) -> tuple[str, str] | None:
    """Return the ``(namespace, canonical key)`` identity of a stored record."""
    extra = record.to_dict()
    registration = extra.get("registration")
    if isinstance(registration, Mapping):
        try:
            parsed = Registration.from_record_value(registration)
        except ValidationError:
            return None
        return (parsed.namespace, canonical_json(parsed.key))
    legacy = from_legacy_record(extra)
    if legacy is not None:
        return (legacy.namespace, canonical_json(legacy.key))
    return None


def _registration_of(record: Record) -> Registration | None:
    """Return the stored registration of ``record``, or its legacy mapping."""
    extra = record.to_dict()
    registration = extra.get("registration")
    if isinstance(registration, Mapping):
        try:
            return Registration.from_record_value(registration)
        except ValidationError:
            return None
    return from_legacy_record(extra)


# --- Refresh semantics ------------------------------------------------------


def _scope_is_narrower(new: Dataset, existing: Registration) -> bool:
    """Return whether ``new`` scans a strict subset of ``existing``'s roots."""
    new_roots = set(new.roots)
    old_roots = set(existing.roots)
    return bool(new_roots and old_roots and new_roots < old_roots)


def _merge_artifacts(
    dataset: Dataset, existing: Registration
) -> tuple[list[Artifact], bool]:
    """Merge incoming observations with the stored ones.

    An exhaustive scan over an equal or broader scope is authoritative: stored
    artifacts that disappeared are demoted to ``stale`` and retained. A partial,
    unknown, or narrowed scan merges evidence without claiming absence. Returns
    the merged artifacts and whether the set changed.
    """
    incoming = {artifact.key: artifact for artifact in dataset.artifacts}
    if dataset.coverage == "exhaustive" and not _scope_is_narrower(dataset, existing):
        merged = dict(incoming)
        for artifact in existing.artifacts:
            if artifact.key in merged:
                continue
            if artifact.availability == "present":
                merged[artifact.key] = artifact.model_copy(
                    update={"availability": "stale"}
                )
            else:
                merged[artifact.key] = artifact
    else:
        merged = {artifact.key: artifact for artifact in existing.artifacts}
        merged.update(incoming)

    result = [merged[key] for key in sorted(merged)]
    return result, _artifacts_signature(result) != _artifacts_signature(
        existing.artifacts
    )


def _artifacts_signature(artifacts: list[Artifact]) -> str:
    """Return the digest of an artifact list, excluding observation times."""
    return content_digest([artifact.model_dump(mode="json") for artifact in artifacts])


def _observation(registration: Registration) -> Observation:
    """Summarize the stored observation that a refresh is about to replace."""
    states = [artifact.availability for artifact in registration.artifacts]
    return Observation(
        observed_at=registration.observed_at,
        coverage=registration.coverage,
        artifacts=len(registration.artifacts),
        present=states.count("present"),
        missing=states.count("missing"),
        stale=states.count("stale"),
    )


def _registration_for(
    dataset: Dataset,
    *,
    source: str,
    existing: Registration | None = None,
    record_type: str = "dataset",
) -> Registration:
    """Convert a manifest dataset into the record's ``registration`` block."""
    if existing is None:
        registration = Registration(
            schema=1,
            namespace=dataset.namespace,
            key=dataset.key,
            source=source,
            parent=dataset.parent,
            metadata=dataset.metadata,
            coverage=dataset.coverage,
            roots=dataset.roots,
            availability=dataset.availability,
            observed_at=dataset.observed_at,
            # Canonical artifact order, matching the merge path, so manifest
            # order alone can never cause an otherwise identical rescan to
            # rewrite a record.
            artifacts=sorted(dataset.artifacts, key=lambda artifact: artifact.key),
            documentation=dataset.documentation,
            managed=_managed_fields(record_type, dataset),
        )
        return registration

    artifacts, changed = _merge_artifacts(dataset, existing)
    history = list(existing.history)
    if changed:
        history.append(_observation(existing))
    return Registration(
        schema=1,
        namespace=dataset.namespace,
        key=dataset.key,
        source=source,
        parent=dataset.parent,
        metadata=dataset.metadata,
        coverage=dataset.coverage,
        roots=dataset.roots,
        availability=dataset.availability,
        observed_at=dataset.observed_at,
        artifacts=artifacts,
        documentation=dataset.documentation,
        managed=_managed_fields(record_type, dataset),
        history=history[-_HISTORY_LIMIT:],
    )


def _managed_fields(record_type: str, dataset: Dataset) -> dict[str, Any]:
    """Return the baseline of the top-level record fields the importer owns."""
    managed: dict[str, Any] = {"type": record_type}
    if dataset.parent is not None:
        managed["parent_id"] = collection_record_id(dataset.namespace, dataset.parent)
    return managed


def collection_record_id(namespace: str, parent: str | list[str]) -> str:
    """Derive the deterministic ID of a collection record for ``parent``."""
    return f"collection-{digest([namespace, parent])}"


def _managed_conflict(
    record: Record,
    registration: Registration,
    override: bool,
    *,
    baselines: dict[str, Any] | None = None,
) -> str | None:
    """Return a human-edit conflict message, or ``None`` when safe to write.

    ``baselines`` are the values saved by the previous import. Legacy records
    written before the generic schema have no such baselines, so a first
    generic reimport may adopt fields such as ``parent_id`` instead of
    reporting a human edit. Once a generic import has run, its saved baselines
    detect later human edits.
    """
    managed = registration.managed if baselines is None else baselines
    for key, baseline in managed.items():
        current = record.to_dict().get(key)
        if current != baseline and not override:
            return (
                f"importer-owned field {key!r} was edited: stored "
                f"{current!r} != {baseline!r}; pass --override-conflicts to "
                "overwrite"
            )
    return None


def _owned_fields_match(record: Record, registration: Registration) -> bool:
    """Return whether the record's importer-owned fields still hold their baseline."""
    for key, baseline in registration.managed.items():
        if record.to_dict().get(key) != baseline:
            return False
    return True


def _plan_collections(
    manifest: RegistrationManifest,
    by_id: dict[str, Record],
    by_identity: dict[tuple[str, str], Record],
) -> dict[tuple[str, str], str]:
    """Resolve collection records for each distinct dataset parent."""
    resolved: dict[tuple[str, str], str] = {}
    for dataset in manifest.datasets:
        if dataset.parent is None:
            continue
        identity = (dataset.namespace, canonical_json(dataset.parent))
        if identity in resolved:
            continue
        record_id = collection_record_id(dataset.namespace, dataset.parent)
        current = by_identity.get(identity) or by_id.get(record_id)
        resolved[identity] = current.id if current is not None else record_id
    return resolved


def plan_import(
    manifest: RegistrationManifest,
    config: HirakuConfig,
    *,
    override: bool = False,
) -> ImportPlan:
    """Compute the import plan without writing anything.

    Collections are planned before their datasets. A record whose ID is already
    taken by a different dataset identity, whose source disagrees, or whose
    importer-owned fields were human-edited is reported as a conflict rather
    than overwritten, unless ``override`` explicitly resolves the field edits.
    """
    manifest = validate_manifest(manifest)
    verify_target(manifest, config)
    records = list_records(config=config)
    by_id = {record.id: record for record in records}
    by_identity: dict[tuple[str, str], Record] = {}
    for record in records:
        identity = _identity_of(record)
        if identity is not None:
            by_identity[identity] = record

    plan = ImportPlan(
        project=config.root,
        source=manifest.source,
        content_sha256=manifest.content_sha256 or manifest.compute_digest(),
    )
    collections = _plan_collections(manifest, by_id, by_identity)
    writes: list[_Write] = []
    planned_ids: set[str] = set()

    for identity, record_id in collections.items():
        current = by_identity.get(identity) or by_id.get(record_id)
        if current is None and record_id not in planned_ids:
            namespace, parent_key = identity
            parent = json.loads(parent_key)
            writes.append(
                _Write(
                    record_id=record_id,
                    record_type="collection",
                    registration=Registration(
                        schema=1,
                        namespace=namespace,
                        key=parent,
                        source=manifest.source,
                        managed={"type": "collection"},
                    ),
                    create=True,
                )
            )
            planned_ids.add(record_id)

    for dataset in manifest.datasets:
        record_id = dataset.record_id()
        identity = (dataset.namespace, canonical_json(dataset.key))
        current = by_identity.get(identity)
        if current is None:
            if record_id in by_id or record_id in planned_ids:
                plan.conflicts.append(
                    Conflict(
                        record_id,
                        "record ID already exists for a different dataset or source",
                    )
                )
                continue
            parent_id = (
                collections[(dataset.namespace, canonical_json(dataset.parent))]
                if dataset.parent is not None
                else None
            )
            writes.append(
                _Write(
                    record_id=record_id,
                    record_type="dataset",
                    registration=_registration_for(dataset, source=manifest.source),
                    parent_id=parent_id,
                    create=True,
                )
            )
            planned_ids.add(record_id)
            continue

        record_id = current.id
        existing = _registration_of(current)
        if existing is not None and existing.source not in (None, manifest.source):
            plan.conflicts.append(
                Conflict(
                    record_id,
                    f"dataset is owned by source {existing.source!r}, not "
                    f"{manifest.source!r}",
                )
            )
            continue
        incoming = _registration_for(dataset, source=manifest.source, existing=existing)
        conflict = _managed_conflict(
            current,
            incoming,
            override,
            baselines=existing.managed if existing is not None else {},
        )
        if conflict is not None:
            plan.conflicts.append(Conflict(record_id, conflict))
            continue
        if (
            existing is not None
            and incoming.semantic_digest() == existing.semantic_digest()
            and _owned_fields_match(current, incoming)
        ):
            plan.unchanged += 1
            continue
        writes.append(
            _Write(
                record_id=record_id,
                record_type="dataset",
                registration=incoming,
                parent_id=(
                    collections[(dataset.namespace, canonical_json(dataset.parent))]
                    if dataset.parent is not None
                    else None
                ),
                create=False,
                existing=current,
            )
        )

    for write in writes:
        (plan.creates if write.create else plan.updates).append(write.record_id)
    plan._writes = writes
    return plan


def apply_import(
    manifest: RegistrationManifest,
    config: HirakuConfig,
    *,
    reviewed_hash: str | None = None,
    override: bool = False,
) -> ImportPlan:
    """Review, verify, and apply a manifest in one step.

    The whole batch is validated and planned first; nothing is written until
    the manifest's digest and the reviewed hash both check out.
    """
    manifest = validate_manifest(manifest, check_digest=True)
    verify_reviewed_hash(manifest, reviewed_hash)
    plan = plan_import(manifest, config, override=override)
    if plan.conflicts:
        return plan
    from .writer import apply_writes

    result = apply_writes(config, plan._writes)
    plan.applied = True
    if result.failed:
        plan.applied = False
        details = "; ".join(
            f"{record_id}: {error}" for record_id, error in result.failed
        )
        raise ValidationError(
            f"registry apply failed for {len(result.failed)} record(s); retry is "
            f"safe: {details}"
        )
    return plan
