"""Versioned schemas and identity rules for dataset registration.

Registration is a generic, read-only capability: it observes datasets and their
artifact descriptors and stores the observations as extra front matter on
ordinary Hiraku records. Nothing here executes workflows, fabricates runs,
copies dataset contents, computes scientific metrics, or infers historical
production provenance.

The one namespaced key Hiraku writes is ``registration`` (``schema: 1``). All
data lives inside it, so main's Hiraku keeps reading the records as normal
records with unknown extra fields. Identity and hashing are specified here and
versioned; adapters emit a :class:`RegistrationManifest` that is validated
before any record is touched.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from datetime import UTC, datetime
from pathlib import PurePosixPath
from typing import Any, Literal, TypeAlias

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from ..errors import ValidationError

SCHEMA_VERSION = 1

Coverage: TypeAlias = Literal["exhaustive", "partial", "unknown"]
Availability: TypeAlias = Literal["present", "missing", "unknown", "stale"]
DocumentationRole: TypeAlias = Literal["input", "output", "unknown"]

_DIGEST_LENGTH = 20
# Record front matter keys excluded from the semantic content digest: observed
# timestamps are observational, and ``managed``/``history`` are importer
# bookkeeping. Excluding them is what makes an unchanged rescan a no-op.
_NON_SEMANTIC = {"observed_at", "managed", "history"}


def canonical_json(value: Any) -> str:
    """Return the canonical JSON used for identity and content digests."""
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def digest(value: Any, *, length: int = _DIGEST_LENGTH) -> str:
    """Return the first ``length`` hex characters of the canonical SHA-256."""
    return hashlib.sha256(canonical_json(value).encode()).hexdigest()[:length]


def content_digest(value: Any, *, length: int = _DIGEST_LENGTH) -> str:
    """Return the digest of ``value`` after removing non-semantic fields.

    Observation timestamps and importer bookkeeping are dropped recursively so
    that re-observing unchanged data yields the same digest.
    """
    return digest(_semantic(value), length=length)


def _semantic(value: Any) -> Any:
    """Recursively drop non-semantic fields from a nested mapping/list."""
    if isinstance(value, Mapping):
        return {
            str(key): _semantic(item)
            for key, item in value.items()
            if key not in _NON_SEMANTIC
        }
    if isinstance(value, (list, tuple)):
        return [_semantic(item) for item in value]
    if isinstance(value, BaseModel):
        return _semantic(value.model_dump(mode="json", exclude=_NON_SEMANTIC))
    return value


def normalize_display_path(value: Any, *, label: str = "display_path") -> str:
    """Validate and normalize a project- or root-relative POSIX display path.

    The path must match the form Snakemake reports for project artifacts: a
    canonical, relative, POSIX path with no traversal. It never encodes
    identity, so relocating a mount does not change an artifact's key.
    """
    if not isinstance(value, str) or not value:
        raise ValidationError(f"{label} must be a non-empty string")
    path = PurePosixPath(value)
    if (
        path.is_absolute()
        or ".." in path.parts
        or "\\" in value
        or "\x00" in value
        or path.as_posix() != value
        or value == "."
    ):
        raise ValidationError(
            f"{label} must be a canonical relative POSIX path: {value!r}"
        )
    return value


def dataset_record_id(namespace: str, key: str | list[str]) -> str:
    """Derive the deterministic Hiraku record ID for a dataset identity."""
    return f"dataset-{digest([namespace, key])}"


class Documentation(BaseModel):
    """One evidence link to source documentation.

    A documentation link is evidence of an expectation, never proof of
    production or of an expected output. Its role and applicability stay
    ``unknown`` when the source does not support them.
    """

    model_config = ConfigDict(extra="allow")

    key: str = Field(min_length=1)
    role: DocumentationRole = "unknown"
    applicability: str | None = None
    filename: str | None = None


class Artifact(BaseModel):
    """One observed artifact descriptor.

    Identity is ``(dataset namespace, key)``; the display path and access
    location are observations, not identity. ``revision`` and ``size`` stay
    ``None`` when unknown.
    """

    model_config = ConfigDict(extra="allow")

    key: str = Field(min_length=1)
    display_path: str
    root: str | None = None
    revision: str | None = None
    size: int | None = Field(default=None, ge=0)
    availability: Availability = "unknown"
    observed_at: datetime | None = None
    access: dict[str, Any] | None = None
    documentation: list[Documentation] = Field(default_factory=list)

    @field_validator("display_path")
    @classmethod
    def _validate_display_path(cls, value: str) -> str:
        return normalize_display_path(value)

    @field_validator("observed_at", mode="before")
    @classmethod
    def _normalize_observed_at(cls, value: Any) -> Any:
        return _coerce_timestamp(value)


class Dataset(BaseModel):
    """A dataset entry in a registration manifest.

    A dataset becomes one normal Hiraku record of ``type: dataset``. The
    namespace scopes its source key; no technology, sample, or SOP field is
    mandatory. ``availability`` summarizes the artifacts and may be ``None``
    until derived.
    """

    model_config = ConfigDict(extra="allow")

    namespace: str = Field(min_length=1)
    key: str | list[str]
    parent: str | list[str] | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)
    coverage: Coverage = "unknown"
    availability: Availability | None = None
    observed_at: datetime | None = None
    artifacts: list[Artifact] = Field(default_factory=list)
    documentation: list[Documentation] = Field(default_factory=list)

    @field_validator("key")
    @classmethod
    def _validate_key(cls, value: Any) -> str | list[str]:
        return _validate_identity_key(value)

    @field_validator("observed_at", mode="before")
    @classmethod
    def _normalize_observed_at(cls, value: Any) -> Any:
        return _coerce_timestamp(value)

    @model_validator(mode="after")
    def _validate_consistency(self) -> Dataset:
        if self.availability in {"missing", "stale"} and self.coverage != "exhaustive":
            raise ValidationError(
                f"dataset {self.key!r} marks artifacts {self.availability} but its "
                "coverage is not exhaustive, so absence is not authoritative"
            )
        for artifact in self.artifacts:
            if (
                artifact.availability in {"missing", "stale"}
                and self.coverage != "exhaustive"
            ):
                raise ValidationError(
                    f"artifact {artifact.key!r} of dataset {self.key!r} is "
                    f"{artifact.availability} under non-exhaustive coverage"
                )
        return self

    def record_id(self) -> str:
        """Return this dataset's deterministic Hiraku record ID."""
        return dataset_record_id(self.namespace, self.key)


class Scope(BaseModel):
    """Explicit coverage and bounds of one source adapter's scan."""

    model_config = ConfigDict(extra="allow")

    coverage: Coverage = "unknown"
    filters: dict[str, Any] = Field(default_factory=dict)
    bounds: dict[str, Any] = Field(default_factory=dict)
    roots: list[str] = Field(default_factory=list)


class RegistrationManifest(BaseModel):
    """The versioned, validated manifest a source adapter emits."""

    model_config = ConfigDict(extra="allow")

    schema_version: Literal[1] = SCHEMA_VERSION
    source: str = Field(min_length=1)
    target: str | None = None
    scope: Scope = Field(default_factory=Scope)
    observed_at: datetime | None = None
    datasets: list[Dataset] = Field(default_factory=list)
    content_sha256: str | None = None

    @field_validator("schema_version", mode="before")
    @classmethod
    def _reject_unknown_schema(cls, value: Any) -> int:
        if isinstance(value, bool) or not isinstance(value, int):
            raise ValidationError("schema_version must be an integer")
        if value != SCHEMA_VERSION:
            raise ValidationError(
                f"unsupported registration schema_version: {value!r}"
            )
        return value

    @field_validator("observed_at", mode="before")
    @classmethod
    def _normalize_observed_at(cls, value: Any) -> Any:
        return _coerce_timestamp(value)

    @model_validator(mode="after")
    def _validate_consistency(self) -> RegistrationManifest:
        identities = [dataset.record_id() for dataset in self.datasets]
        if len(identities) != len(set(identities)):
            raise ValidationError("manifest contains duplicate dataset identities")
        return self

    def compute_digest(self, *, length: int = _DIGEST_LENGTH) -> str:
        """Return the canonical digest of the manifest's semantic content.

        ``content_sha256`` and observation timestamps are excluded, so a
        manifest reviewed in a dry run hashes identically at apply time even
        though a little time passed.
        """
        payload = self.model_dump(mode="json", exclude={"content_sha256"})
        return content_digest(payload, length=length)


class Observation(BaseModel):
    """One retained revision-history entry for a registered dataset."""

    model_config = ConfigDict(extra="allow")

    observed_at: datetime | None = None
    coverage: Coverage = "unknown"
    artifacts: int = 0
    present: int = 0
    missing: int = 0
    stale: int = 0

    @field_validator("observed_at", mode="before")
    @classmethod
    def _normalize_observed_at(cls, value: Any) -> Any:
        return _coerce_timestamp(value)


class Registration(BaseModel):
    """The ``registration`` front-matter block stored on a dataset record.

    ``managed`` holds the importer's baselines of the top-level record fields it
    owns, and ``history`` retains bounded past observations. Both are importer
    bookkeeping and excluded from the semantic content digest.
    """

    model_config = ConfigDict(extra="forbid")

    schema: Literal[1] = SCHEMA_VERSION
    namespace: str = Field(min_length=1)
    key: str | list[str]
    parent: str | list[str] | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)
    coverage: Coverage = "unknown"
    availability: Availability | None = None
    observed_at: datetime | None = None
    artifacts: list[Artifact] = Field(default_factory=list)
    documentation: list[Documentation] = Field(default_factory=list)
    managed: dict[str, Any] = Field(default_factory=dict)
    history: list[Observation] = Field(default_factory=list)

    @field_validator("key")
    @classmethod
    def _validate_key(cls, value: Any) -> str | list[str]:
        return _validate_identity_key(value)

    @field_validator("observed_at", mode="before")
    @classmethod
    def _normalize_observed_at(cls, value: Any) -> Any:
        return _coerce_timestamp(value)

    @field_validator("schema", mode="before")
    @classmethod
    def _reject_unknown_schema(cls, value: Any) -> int:
        if isinstance(value, bool) or not isinstance(value, int):
            raise ValidationError("registration schema must be an integer")
        if value != SCHEMA_VERSION:
            raise ValidationError(f"unsupported registration schema: {value!r}")
        return value

    @model_validator(mode="after")
    def _validate_consistency(self) -> Registration:
        if self.availability in {"missing", "stale"} and self.coverage != "exhaustive":
            raise ValidationError(
                f"dataset {self.key!r} marks itself {self.availability} but its "
                "coverage is not exhaustive"
            )
        for artifact in self.artifacts:
            if (
                artifact.availability in {"missing", "stale"}
                and self.coverage != "exhaustive"
            ):
                raise ValidationError(
                    f"artifact {artifact.key!r} of dataset {self.key!r} is "
                    f"{artifact.availability} under non-exhaustive coverage"
                )
        return self

    def record_id(self) -> str:
        """Return this registration's deterministic Hiraku record ID."""
        return dataset_record_id(self.namespace, self.key)

    def semantic_digest(self, *, length: int = _DIGEST_LENGTH) -> str:
        """Return the digest used to decide whether a refresh changes a record."""
        return content_digest(self.model_dump(mode="json"), length=length)

    def to_record_value(self) -> dict[str, Any]:
        """Return the YAML-safe value stored under the ``registration`` key."""
        return self.model_dump(mode="json", exclude_none=False)

    @classmethod
    def from_record_value(cls, value: Any) -> Registration:
        """Validate a stored ``registration`` value read back from a record."""
        if not isinstance(value, Mapping):
            raise ValidationError("registration must be a mapping")
        return cls.model_validate(dict(value))


def _validate_identity_key(value: Any) -> str | list[str]:
    if isinstance(value, str):
        if not value:
            raise ValidationError("identity key must be a non-empty string")
        return value
    if isinstance(value, list):
        if not value or any(
            not isinstance(item, str) or not item for item in value
        ):
            raise ValidationError(
                "identity key list must contain non-empty strings"
            )
        return value
    raise ValidationError("identity key must be a string or a list of strings")


def _coerce_timestamp(value: Any) -> datetime | None:
    if value is None or value == "":
        return None
    if isinstance(value, datetime):
        parsed = value
    elif isinstance(value, str):
        try:
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError as exc:
            raise ValidationError(f"invalid timestamp: {value!r}") from exc
    else:
        raise ValidationError("timestamp must be an ISO-8601 string or null")
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC)


def redact_access(access: Mapping[str, Any] | None) -> dict[str, Any] | None:
    """Return ``access`` with common credential-bearing keys redacted.

    Credentials belong in access configuration, never in manifests or records.
    This is a safety net; adapters should not emit them at all.
    """
    if access is None:
        return None
    redacted = dict(access)
    for key, item in list(redacted.items()):
        lowered = key.lower()
        if any(token in lowered for token in ("password", "secret", "token", "key")):
            redacted[key] = "<redacted>"
        elif isinstance(item, Mapping):
            redacted[key] = redact_access(item)
    return redacted
