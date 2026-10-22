"""Generic dataset registration: observe datasets and their artifacts as records.

Registration stores observations as extra front matter under one namespaced,
versioned ``registration`` key on ordinary Hiraku records. It never executes
workflows, fabricates runs, copies dataset contents, computes scientific
metrics, or infers historical production provenance.
"""

from .review import (
    Conflict,
    ImportPlan,
    apply_import,
    load_manifest,
    plan_import,
    validate_manifest,
    verify_reviewed_hash,
)
from .schema import (
    SCHEMA_VERSION,
    Artifact,
    Availability,
    Coverage,
    Dataset,
    Documentation,
    DocumentationRole,
    Observation,
    Registration,
    RegistrationManifest,
    Scope,
    canonical_json,
    content_digest,
    dataset_record_id,
    digest,
    normalize_display_path,
    redact_access,
)

__all__ = [
    "SCHEMA_VERSION",
    "Artifact",
    "Availability",
    "Conflict",
    "Coverage",
    "Dataset",
    "Documentation",
    "DocumentationRole",
    "ImportPlan",
    "Observation",
    "Registration",
    "RegistrationManifest",
    "Scope",
    "apply_import",
    "canonical_json",
    "content_digest",
    "dataset_record_id",
    "digest",
    "load_manifest",
    "normalize_display_path",
    "plan_import",
    "redact_access",
    "validate_manifest",
    "verify_reviewed_hash",
]
