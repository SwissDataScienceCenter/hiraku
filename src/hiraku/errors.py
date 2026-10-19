"""Public Hiraku exception hierarchy."""


class HirakuError(Exception):
    """Base class for all expected Hiraku failures."""


class ProjectNotFoundError(HirakuError):
    """No Hiraku project marker could be found."""


class ProjectExistsError(HirakuError):
    """A Hiraku project already exists at the requested location."""


class RecordNotFoundError(HirakuError):
    """A metadata record does not exist."""


class RecordExistsError(HirakuError):
    """A metadata record would overwrite an existing record."""


class ValidationError(HirakuError):
    """A Hiraku value or document is invalid."""


class AmbiguousReferenceError(ValidationError):
    """A record reference cannot be resolved to one record."""


class NoActiveRecordError(HirakuError):
    """An operation requires an active research record."""


class GitError(HirakuError):
    """A Git operation failed."""


class JobError(HirakuError):
    """A job could not be submitted, inspected, or controlled."""


class BackendSubmissionError(JobError):
    """A backend submission failed, with whether acceptance is uncertain."""

    def __init__(self, message: str, *, uncertain: bool = False) -> None:
        super().__init__(message)
        self.uncertain = uncertain


class BackendError(HirakuError):
    """A tracking backend failed to return normalized runs."""


class WorkflowError(HirakuError):
    """A workflow, its code, or one of its tools is invalid."""


class SectionNotFoundError(HirakuError):
    """A Markdown body has no section with the requested heading."""


class SessionNotFoundError(HirakuError):
    """A workflow session does not exist."""


class EventRejected(HirakuError):
    """A workflow event was invalid and did not advance its session."""


class NotReady(EventRejected):
    """A valid workflow event whose evidence is not available yet."""
