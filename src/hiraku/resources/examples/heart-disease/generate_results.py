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

from hiraku import Job, Project

EXPERIMENTS = (
    ("logistic-regression", "heart-disease-prediction", "logistic", 0.2),
    ("random-forest", "heart-disease-prediction", "random_forest", 0.2),
    ("validation-split", "random-forest", "random_forest", 0.3),
)
METRICS = ("val/log_loss", "val/accuracy")


def validate_runs(project: Project | Path) -> list[dict[str, str]]:
    """Check the writer schema, experiment attribution and finite measurements."""
    if isinstance(project, Path):
        project = Project.discover(project)
    tree = ast.parse((project.root / "train.py").read_text())
    fields = next(
        ast.literal_eval(node.value)
        for node in tree.body
        if isinstance(node, ast.Assign)
        and any(isinstance(t, ast.Name) and t.id == "RUN_FIELDS" for t in node.targets)
    )
    with (project.root / "runs.csv").open(newline="") as stream:
        reader = csv.DictReader(stream)
        if reader.fieldnames != fields:
            raise ValueError("Unexpected runs CSV header")
        rows = list(reader)
    paths = {record.id.rsplit("/", 1)[-1]: record.id for record in project.vault.list()}
    for row in rows:
        job = Job.load(row["hiraku_job_id"], project=project)
        row.update(
            record_id=job.record_id, commit=job.commit or "", git_state=job.git_state
        )
    expected = Counter(
        (
            paths[identifier],
            model,
            str(seed),
            str(fraction),
        )
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
    if any(not r["timestamp"] or not r["duration"] for r in rows):
        raise ValueError("Missing launch attribution")
    if not all(math.isfinite(float(r[f"metric.{m}"])) for r in rows for m in METRICS):
        raise ValueError("Non-finite training metric")
    return rows


def generate(project: Project) -> None:
    """Generate records and runs in an initialized, empty example project."""
    if list(project.records_dir.rglob("*.md")) or (project.root / "runs.csv").exists():
        raise FileExistsError("Generation requires an example without records or runs")
    root = project.vault.create(
        "heart-disease-prediction",
        type="task",
        state="concluded",
        markdown="# Heart disease classification\n\n"
        "Measured runs on the processed Cleveland dataset with seeds 1, 2 and 3.\n"
        "These small validation comparisons are not clinical evidence.\n",
        title="Heart disease prediction",
    )
    record_ids = {root.id: root.id}
    for identifier, parent, model, fraction in EXPERIMENTS:
        record = project.vault.create(
            f"{record_ids[parent]}/{identifier}",
            title={
                "logistic-regression": "Logistic regression",
                "random-forest": "Random forest",
                "validation-split": "Validation split sensitivity",
            }[identifier],
            parent_id=record_ids[parent],
            type="baseline"
            if model == "logistic"
            else "diagnostic"
            if identifier == "validation-split"
            else "hypothesis",
            state="concluded",
            markdown=f"## {identifier}\n\n"
            f"Model: `{model}`; validation fraction: {fraction}; seeds: 1, 2, 3.\n"
            + (
                "This is a validation-split sensitivity check; its metrics are "
                "not a directly comparable improvement.\n"
                if identifier == "validation-split"
                else ""
            ),
        )
        record_ids[identifier] = record.id
        command = [
            "uv",
            "run",
            "python",
            "train.py",
            "--model",
            model,
            "--validation-fraction",
            str(fraction),
        ]
        for seed in (1, 2, 3):
            job = Job.create(
                [*command, "--seed", str(seed)],
                record_id=record.id,
                project=project,
            ).run()
            status = job.wait()
            if status.exit_code:
                raise RuntimeError(
                    f"Training command for {identifier} failed with "
                    f"exit code {status.exit_code}"
                )
    rows = validate_runs(project)
    for identifier, _, _, _ in EXPERIMENTS:
        record = project.vault.load(record_ids[identifier])
        values = [r for r in rows if r["record_id"] == record.id]
        record.markdown += (
            "\nMean across the three runs:\n\n"
            + "\n".join(
                f"- `{m}`: {sum(float(r[f'metric.{m}']) for r in values) / 3:.8f}"
                for m in METRICS
            )
            + "\n\nNo statistical significance is claimed.\n"
        )
        project.vault.save(record, overwrite=True)
    project.set_active_record(record_ids["random-forest"])


def generate_results(output: Path) -> None:
    """Train in isolation and publish only a complete, validated overlay."""
    output = output.absolute()
    if output.exists():
        raise FileExistsError(f"Output already exists: {output}")
    with TemporaryDirectory(prefix="hiraku-heart-disease-") as temporary:
        project_dir = Path(temporary).resolve() / "project"
        project = Project.create(
            project_dir, example="heart-disease", install_skill=False
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
            subprocess.run(["git", *arguments], cwd=project.root, check=True)
        generate(project)
        output.parent.mkdir(parents=True, exist_ok=True)
        with TemporaryDirectory(prefix=".results-", dir=output.parent) as staging:
            overlay = Path(staging) / "overlay"
            shutil.copytree(project.records_dir, overlay / "hiraku/records")
            shutil.copytree(project.leaderboards_dir, overlay / "hiraku/leaderboards")
            for name in ("hiraku.yml", "runs.csv"):
                shutil.copyfile(project.root / name, overlay / name)
            # Export self-contained attribution without machine-local job history.
            rows = validate_runs(project)
            fields = list(rows[0])
            with (overlay / "runs.csv").open("w", newline="") as stream:
                writer = csv.DictWriter(stream, fieldnames=fields)
                writer.writeheader()
                writer.writerows(rows)
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
        generate(Project.discover())
