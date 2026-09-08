from __future__ import annotations

import argparse

from tracking_writer import record_run


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--val-bpb", type=float, default=1.0)
    parser.add_argument("--peak-vram-mb", type=float, default=1024.0)
    parser.add_argument("--fail", action="store_true")
    args = parser.parse_args()
    metrics = {
        "val_bpb": 0.0 if args.fail else args.val_bpb,
        "training_seconds": 0.01,
        "peak_vram_mb": 0.0 if args.fail else args.peak_vram_mb,
        "mfu_percent": 0.0,
    }
    status = "crashed" if args.fail else "finished"
    run_id = record_run(metrics, status)
    print("---")
    for key, value in metrics.items():
        print(f"{key}: {value:.6f}")
    print(f"hiraku_run_id: {run_id}")
    return 1 if args.fail else 0


if __name__ == "__main__":
    raise SystemExit(main())
