"""Attribution supplied to tracking integrations by a Hiraku launch."""

import os
from typing import Any

from ..errors import ValidationError


def _tracking_metadata(values: dict[str, Any] | None) -> dict[str, Any]:
    """Add current job metadata to the native tracker's metadata mapping."""
    from ..jobs import current_job

    result = dict(values or {})
    job = current_job()
    job_id = job.id if job else os.environ.get("HIRAKU_JOB_ID")
    job_metadata = (
        {
            "record_id": job.record_id,
            "commit": job.commit,
            "git_state": job.git_state,
        }
        if job
        else {}
    )
    for field in ("job_id", "record_id", "commit", "git_state"):
        value = (
            job_id
            if field == "job_id" and job_id
            else job_metadata.get(field) or os.environ.get(f"HIRAKU_{field.upper()}")
        )
        if value:
            key = f"hiraku.{field}"
            if key in result and result[key] != value:
                raise ValidationError(f"Conflicting attribution for {key}")
            result[key] = value
    return result
