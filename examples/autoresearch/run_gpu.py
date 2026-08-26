from __future__ import annotations

import re
import subprocess
import sys

from tracking_writer import record_run


def main() -> int:
    timed_out = False
    try:
        result = subprocess.run(
            [sys.executable, "train.py"],
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            timeout=600,
        )
        output = result.stdout
        return_code = result.returncode
    except subprocess.TimeoutExpired as exc:
        timed_out = True
        output = exc.stdout or ""
        if isinstance(output, bytes):
            output = output.decode(errors="replace")
        output += "\nHiraku wrapper stopped training after the 600-second hard timeout.\n"
        return_code = 124
    print(output, end="")
    values: dict[str, float] = {}
    for key in ("val_bpb", "training_seconds", "peak_vram_mb", "mfu_percent"):
        match = re.search(rf"^{re.escape(key)}:\s+([0-9.]+)$", output, flags=re.MULTILINE)
        if match:
            values[key] = float(match.group(1))
    status = (
        "finished"
        if return_code == 0 and "val_bpb" in values
        else "timeout"
        if timed_out
        else "crashed"
    )
    values.setdefault("val_bpb", 0.0)
    values.setdefault("peak_vram_mb", 0.0)
    run_id = record_run(values, status)
    print(f"hiraku_run_id:   {run_id}")
    return return_code


if __name__ == "__main__":
    raise SystemExit(main())
