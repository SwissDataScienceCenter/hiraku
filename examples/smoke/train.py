from __future__ import annotations

import argparse

from example_tracking import record_run


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--seed", type=int, default=1)
    parser.add_argument("--score", type=float, required=True)
    args = parser.parse_args()

    record_run(
        {"seed": args.seed},
        {"score": args.score},
        run_name=f"seed-{args.seed}-score-{args.score:.2f}",
        table_metrics={"score": f"{args.score:.6f}"},
    )


if __name__ == "__main__":
    main()
