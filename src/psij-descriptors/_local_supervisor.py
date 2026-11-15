"""Run a PSI/J launcher and save its outcome independently of the submitter.

Executed by file path using the submitter's Python and psutil installation.
"""

import json
import os
import subprocess
import sys
import time
from pathlib import Path

import psutil


def supervise(result_path: Path, command: list[str]) -> None:
    exit_code = None
    message = None
    try:
        identity = result_path.with_name("supervisor.tmp")
        identity.write_text(
            json.dumps(
                {"pid": os.getpid(), "create_time": psutil.Process().create_time()}
            ),
            encoding="utf-8",
        )
        identity.replace(result_path.with_name("supervisor.json"))
        with result_path.with_name("launcher.log").open("wb") as output:
            exit_code = subprocess.run(
                command, stdout=output, stderr=subprocess.STDOUT, check=False
            ).returncode
    except (OSError, psutil.Error) as exc:
        message = str(exc)
    temporary = result_path.with_suffix(".tmp")
    try:
        temporary.write_text(
            json.dumps(
                {
                    "exit_code": exit_code,
                    "finished_at": time.time(),
                    "message": message,
                }
            ),
            encoding="utf-8",
        )
        temporary.replace(result_path)
    finally:
        temporary.unlink(missing_ok=True)


if __name__ == "__main__":
    supervise(Path(sys.argv[1]), sys.argv[2:])
