"""Worker process for dashboard launch jobs.

Run with ``python -m hiraku.dashboard.worker REQUEST_JSON``. The controller
writes the request to a temporary directory outside the project; this process
loads the project configuration and executes the launch through the public
:func:`hiraku.launch.launch` API with ``capture_output=False`` so stdout and
stderr stream to the controller-owned log files.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any


def _fail(message: str) -> int:
    print(f"launch failed: {message}", file=sys.stderr)
    return 2


def run(request_path: Path) -> int:
    from ..config import HirakuConfig
    from ..launch import launch
    from ..record import load_record

    try:
        request: dict[str, Any] = json.loads(request_path.read_text(encoding="utf-8"))
        project_root = Path(request["project_root"])
        config = HirakuConfig.load(project_root / "hiraku.yml")
        record = load_record(request["record_id"], config=config)
        entrypoint = record.entrypoint(request["entrypoint"], config=config)
        result = launch(
            entrypoint,
            name=request["entrypoint"],
            args=request.get("args") or {},
            record_id=record.id,
            commit=request.get("commit"),
            dispatch=request.get("dispatch"),
            capture_output=False,
            check=False,
            config=config,
        )
        return result.returncode
    except Exception as exc:
        return _fail(str(exc))


def main(argv: list[str] | None = None) -> int:
    arguments = list(sys.argv[1:] if argv is None else argv)
    if len(arguments) != 1:
        print(
            "usage: python -m hiraku.dashboard.worker REQUEST_JSON",
            file=sys.stderr,
        )
        return 2
    return run(Path(arguments[0]))


if __name__ == "__main__":
    raise SystemExit(main())
