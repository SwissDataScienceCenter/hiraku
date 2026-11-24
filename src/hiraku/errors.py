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


class GitError(HirakuError):
    """A Git operation failed."""


class ExecutionError(HirakuError):
    """A job could not be submitted, stored, or observed."""


class ObservationError(ExecutionError):
    """A native job could not be attached or its status could not be read."""


class BackendError(HirakuError):
    """A tracking backend failed to return normalized runs."""


class WorkflowError(HirakuError):
    """A workflow, its code, or one of its tools is invalid."""


class SectionNotFoundError(HirakuError):
    """A Markdown body has no section with the requested heading."""
