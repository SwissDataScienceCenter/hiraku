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


class NoActiveTaskError(HirakuError):
    """An operation requires an active task."""


class NoActiveExperimentError(HirakuError):
    """An operation requires an active experiment."""


class EntrypointNotFoundError(HirakuError):
    """A named entrypoint is absent from a record."""


class GitError(HirakuError):
    """A Git operation failed."""


class LaunchError(HirakuError):
    """A launch could not be completed successfully."""


class BackendError(HirakuError):
    """A tracking backend failed to return normalized runs."""
