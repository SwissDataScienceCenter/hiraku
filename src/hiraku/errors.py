class HirakuError(Exception):
    """An expected, user-facing Hiraku error."""


class ValidationError(HirakuError):
    """Invalid Hiraku metadata or command input."""
