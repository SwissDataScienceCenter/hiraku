from __future__ import annotations

import argparse


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
    print("---")
    for key, value in metrics.items():
        print(f"{key}: {value:.6f}")
    return 1 if args.fail else 0


if __name__ == "__main__":
    raise SystemExit(main())
