"""Check the entrypoints an `implement` call declares before its commit is bound.

`hiraku launch` runs an entrypoint's `command` plus its `args` rendered as flags: no
shell, no placeholder substitution. A job script and an entrypoint written apart often
disagree (positional arguments against flags, `<run-id>` left in a value, a script
that was renamed). The first launch then fails after queueing, so these are caught
here, while the agent can still fix them.

`problems` are certain failures and stop the bind. `cautions` are likely mistakes found
by reading the script; they are recorded in the node's implementation notes.
"""

from __future__ import annotations

import posixpath
import re
from collections.abc import Callable, Mapping
from typing import Any

from hiraku.errors import HirakuError
from hiraku.launch import render_arguments
from hiraku.record import Entrypoint

SCRIPT_SUFFIXES = (".sh", ".sbatch", ".bash", ".py")

# Text hiraku passes through unchanged but that only a shell or a template would fill.
_PLACEHOLDERS = (
    (re.compile(r"<[^<>\s]+>"), "a `<...>` placeholder"),
    (re.compile(r"\{\{[^{}]*\}\}|\{[A-Za-z_][\w.-]*\}"), "a `{...}` placeholder"),
    (re.compile(r"\$\{?[A-Za-z_(]"), "a `$` expansion"),
    (re.compile(r"`"), "a backtick expansion"),
    (re.compile(r"UNSET"), "an UNSET marker"),
    (re.compile(r"^~(/|$)"), "a `~` path"),
)

_POSITIONAL = re.compile(r"\$\{?1\b")
_OPTION_PARSING = re.compile(r'case\s+"?\$\{?1|getopts|\$@|\$\{@|\$\*|argparse')


def entrypoint_problems(
    entrypoints: Mapping[str, Any],
    *,
    read_file: Callable[[str], str | None],
) -> tuple[list[str], list[str]]:
    """`(problems, cautions)` for the entrypoints as `hiraku launch` would run them.

    `read_file(path)` returns the text of `path` (relative to the project directory)
    in the commit about to be bound, or `None` if the commit has no such file.
    """
    problems: list[str] = []
    cautions: list[str] = []
    for name, raw in entrypoints.items():
        try:
            entry = Entrypoint.model_validate(raw)
            flags = render_arguments(entry.args)
        except (HirakuError, ValueError) as exc:
            problems.append(f"entrypoint {name!r} is not valid: {exc}")
            continue
        command = [*entry.command, *flags]
        for token in command:
            if token == "":
                problems.append(f"entrypoint {name!r} passes an empty argument")
                continue
            for pattern, what in _PLACEHOLDERS:
                if pattern.search(token):
                    problems.append(
                        f"entrypoint {name!r}: `{token}` contains {what}; hiraku "
                        f"passes it unchanged, so the program would receive it "
                        f"literally"
                    )
                    break
        scripts = _scripts(entry.command)
        for script in scripts:
            text = read_file(script)
            if text is None:
                problems.append(
                    f"entrypoint {name!r} runs `{script}`, which is not in the commit "
                    f"(paths are relative to the project directory)"
                )
                continue
            first = flags[0] if flags else None
            if (
                first is not None
                and first.startswith("-")
                and _POSITIONAL.search(text)
                and not _OPTION_PARSING.search(text)
            ):
                cautions.append(
                    f"entrypoint {name!r} passes `{first}` as the first argument to "
                    f"`{script}`, but the script reads its arguments by position "
                    f"(`$1`) and does not parse options; it would take the flag as a "
                    f"value. Check the two against each other."
                )
    return problems, cautions


def _scripts(command: list[str]) -> list[str]:
    """Paths in `command` that name a script the commit must contain."""
    found: list[str] = []
    for token in command:
        if token.startswith(("-", "/")) or "://" in token or "=" in token:
            continue
        if not token.endswith(SCRIPT_SUFFIXES):
            continue
        path = posixpath.normpath(token)
        if path.startswith(".."):
            continue
        found.append(path)
    return found
