"""Generate a fresh heart-disease results overlay using the bundled training code."""

from __future__ import annotations

import argparse
import ast
import csv
import math
import shutil
import subprocess
from collections import Counter
from pathlib import Path
from tempfile import TemporaryDirectory

from hiraku import HirakuConfig, submit_job, wait_job
from hiraku.record import Record

EXPERIMENTS = (
    ("logistic-baseline", "heart-disease", "logistic", 0.2),
    ("random-forest", "heart-disease", "random_forest", 0.2),
    ("forest-tuning", "random-forest", "random_forest", 0.3),
)
METRICS = ("val/log_loss", "val/accuracy")


def validate_runs(project: Path) -> list[dict[str, str]]:
    """Check the writer schema, experiment attribution and finite measurements."""
    tree = ast.parse((project / "train.py").read_text())
    fields = next(
        ast.literal_eval(node.value)
        for node in tree.body
        if isinstance(node, ast.Assign)
        and any(isinstance(t, ast.Name) and t.id == "RUN_FIELDS" for t in node.targets)
    )
    with (project / "runs.csv").open(newline="") as stream:
        reader = csv.DictReader(stream)
        if reader.fieldnames != fields:
            raise ValueError("Unexpected runs CSV header")
        rows = list(reader)
    expected = Counter(
        (identifier, model, str(seed), str(fraction))
        for identifier, _, model, fraction in EXPERIMENTS
        for seed in (1, 2, 3)
    )
    actual = Counter(
        (
            r["record_id"],
            r["param.model"],
            r["param.seed"],
            r["param.validation_fraction"],
        )
        for r in rows
    )
    if actual != expected or len({r["id"] for r in rows}) != 9:
        raise ValueError("Expected three uniquely identified runs per experiment")
    if any(not r["job_id"] or not r["timestamp"] or not r["duration"] for r in rows):
        raise ValueError("Missing launch attribution")
    if not all(math.isfinite(float(r[f"metric.{m}"])) for r in rows for m in METRICS):
        raise ValueError("Non-finite training metric")
    return rows


def generate(config: HirakuConfig) -> None:
    """Generate records and runs in an initialized, empty example project."""
    if list(config.records_dir.rglob("*.md")) or (config.root / "runs.csv").exists():
        raise FileExistsError("Generation requires an example without records or runs")
    Record.create(
        "heart-disease",
        type="task",
        status="concluded",
        activate=False,
        metrics=[
            {"name": m, "direction": d}
            for m, d in zip(METRICS, ("minimize", "maximize"), strict=True)
        ],
        markdown="# Heart disease classification\n\n"
        "Measured runs on the processed Cleveland dataset with seeds 1, 2 and 3.\n"
        "These small validation comparisons are not clinical evidence.\n",
        config=config,
    )
    for identifier, parent, model, fraction in EXPERIMENTS:
        record = Record.create(
            identifier,
            parent_id=parent,
            type="baseline"
            if model == "logistic"
            else "diagnostic"
            if identifier == "forest-tuning"
            else "hypothesis",
            status="concluded",
            activate=False,
            config=config,
            markdown=f"## {identifier}\n\n"
            f"Model: `{model}`; validation fraction: {fraction}; seeds: 1, 2, 3.\n"
            + (
                "This is a validation-split sensitivity check; its metrics are "
                "not a directly comparable improvement.\n"
                if identifier == "forest-tuning"
                else ""
            ),
            command=[
                "uv",
                "run",
                "python",
                "train.py",
                "--model",
                model,
                "--validation-fraction",
                str(fraction),
            ],
        )
        for seed in (1, 2, 3):
            job = submit_job(
                [*record.command, "--seed", str(seed)],
                record_id=identifier,
                config=config,
            )
            completed = wait_job(job.id, config=config)
            if completed.status != "succeeded":
                raise RuntimeError(f"Training job {job.id} failed: {completed.error}")
    rows = validate_runs(config.root)
    for identifier, _, _, _ in EXPERIMENTS:
        values = [r for r in rows if r["record_id"] == identifier]
        record = Record.load(config.records_dir / "heart-disease" / f"{identifier}.md")
        record.markdown += (
            "\nMean across the three runs:\n\n"
            + "\n".join(
                f"- `{m}`: {sum(float(r[f'metric.{m}']) for r in values) / 3:.8f}"
                for m in METRICS
            )
            + "\n\nNo statistical significance is claimed.\n"
        )
        record.save(overwrite=True)
    config.active_record_id = "random-forest"
    config.save()


def generate_results(output: Path) -> None:
    """Train in isolation and publish only a complete, validated overlay."""
    output = output.absolute()
    if output.exists():
        raise FileExistsError(f"Output already exists: {output}")
    with TemporaryDirectory(prefix="hiraku-heart-disease-") as temporary:
        project = Path(temporary).resolve() / "project"
        config = HirakuConfig.init(
            project, example="heart-disease", install_skill=False
        )
        for arguments in (
            ["init", "-q"],
            ["add", "."],
            [
                "-c",
                "user.name=Hiraku example",
                "-c",
                "user.email=example@hiraku.invalid",
                "-c",
                "commit.gpgsign=false",
                "commit",
                "-qm",
                "Initialize isolated example source",
            ],
        ):
            subprocess.run(["git", *arguments], cwd=config.root, check=True)
        generate(config)
        output.parent.mkdir(parents=True, exist_ok=True)
        with TemporaryDirectory(prefix=".results-", dir=output.parent) as staging:
            overlay = Path(staging) / "overlay"
            shutil.copytree(config.records_dir, overlay / "hiraku/records")
            for name in ("hiraku.yml", "runs.csv"):
                shutil.copyfile(project / name, overlay / name)
            overlay.rename(output)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--output", type=Path, help="Export an isolated results overlay"
    )
    args = parser.parse_args()
    if args.output is not None:
        generate_results(args.output)
    else:
        generate(HirakuConfig.discover())
