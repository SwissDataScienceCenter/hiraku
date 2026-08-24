"""Error types. One user-facing exception, caught at the CLI boundary."""

from __future__ import annotations


class HirakuError(Exception):
    """An error with a message meant for the user, not a stack trace."""


class AgentError(HirakuError):
    """The agent command failed, was not found, or returned unusable output."""
