"""Exceptions specific to `hiraku_agent`'s LLM-calling layer.

Subclasses `hiraku.errors.HirakuError` on purpose: `hiraku_agent.cli` already
catches `HirakuError` and turns it into a `click.ClickException` with the
same formatting `hiraku`'s own CLI uses, so an agent failure reads like any
other `hiraku-agent` error instead of a raw traceback.
"""

from __future__ import annotations

from hiraku.errors import HirakuError


class AgentError(HirakuError):
    """The agent command failed, timed out, or returned something unusable."""
