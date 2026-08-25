#!/usr/bin/env python3
"""Regenerate docs/autoresearch/commands.md from the CLI parser.

    python tools/gen_commands_doc.py            # write
    python tools/gen_commands_doc.py --check    # exit 1 if out of date

Generated rather than written by hand, so the reference cannot drift from the parser.
`tests/test_commands_doc.py` runs the check.
"""

from __future__ import annotations

import argparse
import contextlib
import io
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from hiraku_auto.cli import build_parser  # noqa: E402

DOC = Path(__file__).resolve().parent.parent / "docs" / "autoresearch" / "commands.md"

HEADER = """# Command reference

Generated from the CLI parser by `tools/gen_commands_doc.py`. Do not edit by hand.

`--project <path>` works on every command and points at a project without `cd`-ing into it;
without it the project is found by walking up from the working directory to the nearest
`.hiraku.yml`.

Guides: [cli.md](cli.md) for the walkthrough and the environment/container setup,
[research-loop.md](research-loop.md) for what the triage and promotion rules mean.

## At a glance

| Command | Does |
|---|---|
| `init <path> --git` | create a research project, in its own repository |
| `goal add\\|list\\|show` | research questions: metric, direction, target, gate metric |
| `exp create\\|list\\|show\\|set` | experiments; `--parent` is what makes one a sub-experiment |
| `tree` | the tree, from declared `parent` links |
| `check` | every consistency problem the records can express |
| `sync` | regenerate the Children sections |
| `launch <exp>:<entrypoint>` | run it, record provenance and metrics |
| `runs` | list or rank runs |
| `agent design\\|analyze\\|triage\\|discover` | call Claude Code; returns JSON, tool writes |
| `next` | what the loop would do next, without calling anything |
| `loop` | bounded loop, stopping wherever a human decides |
| `promote --run <id>` | the five-condition gate |
| `report <goal>` | assemble the goal's report from the records |
| `proposal list\\|accept\\|decline` | the agent's pending queue |
| `worktree add\\|remove\\|list` | one checkout per active experiment |
| `dash` / `ui` | the static page / the Streamlit app |

## Full help
"""


def render() -> str:
    out = [HEADER]

    def dump(parser: argparse.ArgumentParser, path: str) -> None:
        buffer = io.StringIO()
        with contextlib.redirect_stdout(buffer):
            parser.print_help()
        title = f"hiraku-auto {path}".strip()
        out.append(f"\n### `{title} --help`\n")
        out.append("```\n" + buffer.getvalue().rstrip() + "\n```\n")

    def walk(parser: argparse.ArgumentParser, path: str = "") -> None:
        dump(parser, path)
        for action in parser._actions:
            if isinstance(action, argparse._SubParsersAction):
                for name, sub in action.choices.items():
                    walk(sub, f"{path}{name} ")

    walk(build_parser())
    return "".join(out)


def main() -> int:
    text = render()
    if "--check" in sys.argv:
        current = DOC.read_text(encoding="utf-8") if DOC.is_file() else ""
        if current != text:
            print(
                f"{DOC.name} is out of date. Regenerate:\n  python tools/gen_commands_doc.py",
                file=sys.stderr,
            )
            return 1
        print(f"{DOC.name} is up to date")
        return 0
    DOC.parent.mkdir(parents=True, exist_ok=True)
    DOC.write_text(text, encoding="utf-8")
    print(f"wrote {DOC}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
