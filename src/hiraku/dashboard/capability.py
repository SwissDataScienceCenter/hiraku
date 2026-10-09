"""Capability boundary for dashboard actions.

The dashboard is read-only by default. Mutation and launch services receive a
``DashboardCapability`` and reject operations when interactive mode was not
enabled explicitly at startup. Hiding buttons in the notebook is not
sufficient: every service that writes project files or spawns a launch worker
must enforce this boundary itself.
"""

from __future__ import annotations

from dataclasses import dataclass

from ..errors import ValidationError


@dataclass(frozen=True)
class DashboardCapability:
    """Plain Python capability object shared by action and job services."""

    interactive: bool = False

    def ensure_can_write(self) -> None:
        if not self.interactive:
            raise ValidationError(
                "Record creation requires interactive mode. Start the "
                "dashboard with `hiraku dashboard --interactive`."
            )

    def ensure_can_launch(self) -> None:
        if not self.interactive:
            raise ValidationError(
                "Launching entrypoints requires interactive mode. Start the "
                "dashboard with `hiraku dashboard --interactive`."
            )
