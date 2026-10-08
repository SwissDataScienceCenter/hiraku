"""Execute a command with a POSIX per-process CPU time limit."""

from __future__ import annotations

import os
import sys


def main() -> None:
    if os.name != "posix":
        raise SystemExit("CPU time limits require a POSIX host")
    if len(sys.argv) < 3:
        raise SystemExit(
            "usage: python -m hiraku.core.cpu_limit SECONDS COMMAND [ARGS...]"
        )
    try:
        seconds = int(sys.argv[1])
    except ValueError as exc:
        raise SystemExit("CPU limit must be an integer") from exc
    if seconds < 1:
        raise SystemExit("CPU limit must be positive")

    import resource

    resource.setrlimit(resource.RLIMIT_CPU, (seconds, seconds))
    command = sys.argv[2:]
    os.execvpe(command[0], command, os.environ)


if __name__ == "__main__":
    main()
