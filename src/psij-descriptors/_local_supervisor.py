"""Run a PSI/J launcher and save its outcome independently of the submitter.

Executed by file path so the job environment needs only Python's standard library.
"""

import json
import subprocess
import sys
import time
from pathlib import Path


def supervise(result_path: Path, command: list[str]) -> None:
    exit_code = None
    message = None
    try:
        with result_path.with_name("launcher.log").open("wb") as output:
            exit_code = subprocess.run(
                command, stdout=output, stderr=subprocess.STDOUT, check=False
            ).returncode
    except OSError as exc:
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
