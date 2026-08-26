"""Run an example with the dependencies required by its tracking backend."""

from __future__ import annotations

import os
import subprocess
import sys


def main() -> int:
    backend = os.environ.get("HIRAKU_TRACKING_BACKEND", "table")
    command = ["uv", "run"]
    if backend == "mlflow":
        command.extend(["--extra", "mlflow"])
    command.extend(["python", *sys.argv[1:]])
    return subprocess.run(command, check=False).returncode


if __name__ == "__main__":
    raise SystemExit(main())
