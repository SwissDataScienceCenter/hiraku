"""The command reference is generated, so it cannot drift from the parser."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def test_commands_doc_is_up_to_date():
    result = subprocess.run(
        [sys.executable, str(ROOT / "tools" / "gen_commands_doc.py"), "--check"],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr


def test_every_subcommand_appears_in_the_doc():
    sys.path.insert(0, str(ROOT / "src"))
    import argparse

    from hiraku_auto.cli import build_parser

    doc = (ROOT / "docs" / "autoresearch" / "commands.md").read_text(encoding="utf-8")

    def walk(parser, path=""):
        assert f"`hiraku-auto {path}--help`".replace(" --help", " --help") in doc or (
            f"`hiraku-auto {path.strip()} --help`" in doc
        ), path
        for action in parser._actions:
            if isinstance(action, argparse._SubParsersAction):
                for name, sub in action.choices.items():
                    walk(sub, f"{path}{name} ")

    walk(build_parser())
