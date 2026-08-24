"""hiraku-auto — autoresearch over a Hiraku experiment tree.

Goals decompose into parallel hypotheses; a hypothesis is an experiment with a `parent`, so the
record is a tree of arbitrary depth. Markdown records are the source of truth; this package
generates and reads them, launches runs with provenance, gates promotions, and calls an agent to
design, triage, discover and analyse.
"""

from __future__ import annotations

__version__ = "0.1.0"

from .errors import AgentError, HirakuError

__all__ = ["HirakuError", "AgentError", "__version__"]
