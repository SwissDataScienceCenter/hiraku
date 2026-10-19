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


class EntrypointNotFoundError(HirakuError):
    """A named entrypoint is absent from a record."""


class GitError(HirakuError):
    """A Git operation failed."""


class LaunchError(HirakuError):
    """A launch could not be completed successfully."""


class BackendError(HirakuError):
    """A tracking backend failed to return normalized runs."""


class WorkflowError(HirakuError):
    """A workflow, its code, or one of its tools is invalid."""


class SectionNotFoundError(HirakuError):
    """A Markdown body has no section with the requested heading."""


class SessionNotFoundError(HirakuError):
    """A workflow session does not exist."""


class SubmissionRejected(HirakuError):
    """A workflow reply was invalid and did not advance its session."""


class NotReady(SubmissionRejected):
    """A valid workflow reply whose evidence is not available yet."""
