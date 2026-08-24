"""Agent prompt templates, shipped as package data.

Each file states the rules for one workflow and ends with a strict JSON output contract. The agent
decides; this package writes the files. Keeping the contract narrow is what makes an agent's
contribution auditable — every field it returns ends up in a record a human can read and revert.
"""

from pathlib import Path

PROMPT_DIR = Path(__file__).parent

__all__ = ["PROMPT_DIR"]
