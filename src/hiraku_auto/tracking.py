"""Mirroring runs into an external tracker — MLflow today, room for others.

The run table stays the source of truth. That is the whole design, and it is not negotiable
without breaking the promotion gate: conditions 2, 3 and 4 compare `run_kind`, `lockfile_sha256`,
`container_digest` and the goal record *as it stood at the run's commit*. MLflow has no column for
any of those, so a project whose runs lived only in MLflow could not evaluate its own gate, and
`hiraku-auto check` would need a network connection to answer a question about local files.

So the backend value is `table` or `table+mlflow`. There is deliberately no `mlflow` alone.

What the mirror gives you is the MLflow UI, its comparison plots, and a place colleagues already
look — for free, and with the Hiraku run id carried across in both directions:

    runs.csv                              MLflow
    run_id       r-2026...-f0d6    <-->   tag  hiraku.run_id
    mlflow_run_id  4b1c...                run  4b1c...

The mapping of the hierarchy is deliberate. An MLflow *experiment* is "a set of comparable runs";
a Hiraku *goal* is "the experiments that share one evaluation protocol". Those are the same idea,
so a goal maps to an MLflow experiment and a Hiraku experiment becomes a tag on the run.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import Any

from .config import Project
from .errors import HirakuError

BACKENDS = ("table", "table+mlflow")

# A run that failed to mirror is still a recorded run. Losing the row because a tracking server
# was down would be a far worse failure than an un-mirrored run, so mirroring never raises past
# `log_run` — it returns the reason, and the reason is printed and kept in the row.
MIRROR_COLUMN = "mlflow_run_id"


@dataclass
class Resolved:
    """The tracking decision for one experiment, and where each part of it came from."""

    backend: str = "table"
    tracking_uri: str = ""
    experiment_name: str = ""
    tags: dict[str, str] = field(default_factory=dict)
    sources: list[str] = field(default_factory=list)
    narrowed_by: str = ""

    @property
    def mirrors(self) -> bool:
        return self.backend == "table+mlflow"


# --------------------------------------------------------------------------- configuration


def _project_block(project: Project) -> dict[str, Any]:
    """`tracking:` merged from `.hiraku.yml` and `research/config.md`, the latter winning.

    `.hiraku.yml` owns machine paths; `research/config.md` is the human-facing contract and is
    where a researcher looks for it. Merging key by key means neither file has to repeat the other.
    """
    merged: dict[str, Any] = {}
    raw = project.raw.get("tracking")
    if isinstance(raw, dict):
        merged.update(raw)
    if project.config_md.is_file():
        from .records import read_record

        block = read_record(project.config_md).front.get("tracking")
        if isinstance(block, dict):
            merged.update(block)
    return merged


def _experiment_block(project: Project, experiment_id: str | None) -> dict[str, Any]:
    """`tracking:` from `experiments/<id>/launch.yml`, if the file has one."""
    if not experiment_id:
        return {}
    path = project.experiments_dir / experiment_id / "launch.yml"
    if not path.is_file():
        return {}
    from . import yamlio

    block = yamlio.load_file(path).get("tracking")
    return block if isinstance(block, dict) else {}


def resolve(
    project: Project, experiment_id: str | None = None, goal_id: str | None = None
) -> Resolved:
    """What tracking this run gets, and why.

    An experiment may NARROW the project's choice — turn the mirror off — and may never widen it.
    Same rule as `mode` and the project bounds: a node that could switch mirroring on for itself
    could send data somewhere the project never agreed to.
    """
    block = _project_block(project)
    out = Resolved()

    backend = str(block.get("backend") or "table").strip()
    if backend not in BACKENDS:
        raise HirakuError(
            f"tracking.backend '{backend}' is not one of: {', '.join(BACKENDS)}.\n"
            f"There is no 'mlflow' alone on purpose — the run table is what the promotion gate "
            f"reads, so it is always written."
        )
    out.backend = backend
    out.sources.append("research/config.md" if project.config_md.is_file() else ".hiraku.yml")

    mlflow_cfg = block.get("mlflow")
    if isinstance(mlflow_cfg, dict):
        out.tracking_uri = str(mlflow_cfg.get("tracking_uri") or "")
        named = mlflow_cfg.get("experiment")
        out.experiment_name = str(named) if named else ""
        extra = mlflow_cfg.get("tags")
        if isinstance(extra, dict):
            out.tags.update({str(k): str(v) for k, v in extra.items()})

    # The goal names its own MLflow experiment, or lends it its id.
    if goal_id:
        from .records import goal_path, read_record

        path = goal_path(project, goal_id)
        if path.is_file():
            declared = read_record(path).front.get("mlflow_experiment")
            if declared:
                out.experiment_name = str(declared)
        if not out.experiment_name:
            out.experiment_name = goal_id

    # An experiment may only narrow.
    local = _experiment_block(project, experiment_id)
    if local:
        wanted = str(local.get("backend") or out.backend).strip()
        if wanted not in BACKENDS:
            raise HirakuError(
                f"tracking.backend '{wanted}' in experiments/{experiment_id}/launch.yml is not "
                f"one of: {', '.join(BACKENDS)}."
            )
        if wanted == "table" and out.backend != "table":
            out.backend = "table"
            out.narrowed_by = f"experiments/{experiment_id}/launch.yml"
        elif wanted != out.backend:
            raise HirakuError(
                f"experiments/{experiment_id}/launch.yml asks for '{wanted}' but the project is "
                f"set to '{out.backend}'. An experiment may narrow tracking (to 'table'), never "
                f"widen it — otherwise a node could send data somewhere the project never agreed "
                f"to. Change research/config.md if the project should mirror."
            )
    if experiment_id:
        out.tags.setdefault("hiraku.experiment_id", experiment_id)
    if goal_id:
        out.tags.setdefault("hiraku.goal", goal_id)
    return out


# --------------------------------------------------------------------------- the mirror


def _import_mlflow() -> Any:
    try:
        import mlflow  # noqa: PLC0415
    except ImportError as exc:
        raise HirakuError(
            "tracking.backend is 'table+mlflow' but mlflow is not importable.\n"
            "  pip install mlflow            # or `pip install -e '.[mlflow]'` in the tool repo\n"
            "Set tracking.backend to 'table' in research/config.md to run without it."
        ) from exc
    return mlflow


def check(project: Project, resolved: Resolved) -> str:
    """Is the mirror usable? Returns '' when it is, otherwise the reason it is not.

    Called by `hiraku-auto tracking` so a broken server is found before a six-hour run, not after.
    """
    if not resolved.mirrors:
        return ""
    try:
        mlflow = _import_mlflow()
    except HirakuError as exc:
        return str(exc).splitlines()[0]
    uri = resolved.tracking_uri or os.environ.get("MLFLOW_TRACKING_URI") or ""
    if not uri:
        return (
            "no tracking_uri: set tracking.mlflow.tracking_uri in research/config.md, or export "
            "MLFLOW_TRACKING_URI. Without one, mlflow writes to ./mlruns."
        )
    try:
        client = mlflow.MlflowClient(tracking_uri=uri)
        client.search_experiments(max_results=1)
    except Exception as exc:  # a server problem, not ours to classify
        return f"{type(exc).__name__}: {str(exc)[:200]}"
    return ""


def log_run(
    project: Project,
    resolved: Resolved,
    row: dict[str, Any],
    *,
    metric_keys: tuple[str, ...] = (),
) -> tuple[dict[str, str], str]:
    """Mirror one finished run. Returns (columns to merge into the row, warning or '').

    Never raises. A tracking server being down must not cost you the run row — the row is the
    record, the mirror is a convenience — so a failure comes back as a warning the caller prints
    and this function writes nothing.
    """
    if not resolved.mirrors:
        return {}, ""
    try:
        mlflow = _import_mlflow()
        uri = resolved.tracking_uri or os.environ.get("MLFLOW_TRACKING_URI")
        if uri:
            mlflow.set_tracking_uri(uri)
        mlflow.set_experiment(resolved.experiment_name or "hiraku")

        params, metrics = {}, {}
        for key, value in row.items():
            if key in PROVENANCE_AS_PARAMS:
                params[key] = "" if value is None else str(value)
                continue
            if key in NEVER_MIRRORED:
                continue
            number = _as_float(value)
            if number is None:
                params[key] = str(value)
            else:
                metrics[key] = number
        for key in metric_keys:
            number = _as_float(row.get(key))
            if number is not None:
                metrics[key] = number

        tags = dict(resolved.tags)
        tags["hiraku.run_id"] = str(row.get("run_id", ""))
        tags["hiraku.reproducible"] = str(row.get("reproducible", ""))

        with mlflow.start_run(run_name=str(row.get("run_id", ""))) as active:
            mlflow.set_tags(tags)
            if params:
                mlflow.log_params(params)
            if metrics:
                mlflow.log_metrics(metrics)
            return {MIRROR_COLUMN: active.info.run_id}, ""
    except HirakuError as exc:
        return {}, str(exc).splitlines()[0]
    except Exception as exc:
        return {}, (
            f"mlflow mirror failed ({type(exc).__name__}: {str(exc)[:150]}) "
            f"— the row was still written"
        )


# Provenance that is a fact about the run, not a measurement: MLflow params, never metrics.
PROVENANCE_AS_PARAMS = frozenset({
    "run_id", "experiment_id", "commit", "run_kind", "reproducible", "launched_at", "entrypoint",
    "seed", "python_version", "lockfile_sha256", "interpreter", "venv", "container_image",
    "container_digest", "platform", "accelerator",
})
NEVER_MIRRORED = frozenset({MIRROR_COLUMN})


def _as_float(value: Any) -> float | None:
    if isinstance(value, bool):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None
