"""Reading and writing the markdown records: goals, experiments, proposals.

Every record is front matter plus named level-2 sections. This module is the only place that
touches those files, so field order, validation, and section handling stay in one place.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from . import yamlio
from .config import Project
from .errors import HirakuError

ID_PATTERN = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")

# A value nobody has supplied yet, written in place rather than guessed. The marker exists so that
# "we don't know the dataset checksum" survives as a fact in the record instead of being smoothed
# over by a plausible sentence — `check` reports it, and the report prints it.
UNSET_PATTERN = re.compile(r"\*\*\[UNSET:\s*([^\]]*?)\s*\]\*\*")


# A metric name is lower_snake_case in backticks. Any such name used ANYWHERE in a goal record
# has to be defined in the Evaluation protocol, or nobody — the researcher included — can say what
# the number means. This is how `pairs_at_cap_fraction` gets into a success criterion undefined.
METRIC_TOKEN = re.compile(r"`([a-z][a-z0-9]*(?:_[a-z0-9]+)+)`")

# The tool's own vocabulary, and common front-matter keys. Mentioning these is not a metric claim.
TOOL_VOCABULARY = frozenset({
    "primary_metric", "gate_metric", "mlflow_experiment", "mlflow_run_id", "run_id",
    "experiment_id", "run_kind", "launched_at", "exit_code", "duration_s", "python_version",
    "lockfile_sha256", "container_image", "container_digest", "agreement_tolerance",
    "reproduction_seeds", "max_runs_per_goal", "max_tree_depth", "max_concurrent_worktrees",
    "max_wallclock_hours_per_run", "max_files", "max_bytes_per_file", "max_total_bytes",
    "tracking_uri", "proposed_parent", "decided_on", "proposed_on", "expectation_body",
    "first_experiments", "how_to_fill", "why_it_matters", "success_criterion",
    "evaluation_protocol", "out_of_scope", "open_threads", "held_fixed", "failure_class",
    "class_evidence", "investigation_test", "blocking_condition", "next_steps",
    "promotable_candidate", "named_cause", "in_scope", "within_budget",
})

# Where a metric counts as defined. The protocol is the one section every experiment inherits, so
# a definition anywhere else is not inherited and does not settle anything.
DEFINING_SECTIONS = ("Evaluation protocol",)


def metric_mentions(record: Record) -> dict[str, list[str]]:
    """Every metric-looking name in a goal record, and which sections mention it."""
    found: dict[str, list[str]] = {}
    for name, text in record.sections.items():
        for match in METRIC_TOKEN.finditer(text):
            token = match.group(1)
            if token in TOOL_VOCABULARY:
                continue
            found.setdefault(token, [])
            if name not in found[token]:
                found[token].append(name)
    for key in ("primary_metric", "gate_metric"):
        value = record.front.get(key)
        if isinstance(value, str) and value and value not in TOOL_VOCABULARY:
            found.setdefault(value, [])
            label = f"front matter `{key}`"
            if label not in found[value]:
                found[value].insert(0, label)
    return found


def undefined_metrics(record: Record) -> list[tuple[str, str]]:
    """Metric names the record uses but never defines, as (name, where it was used).

    A name that appears only in the Success criterion is exactly the failure this catches: the
    criterion reads as precise, and the number it names does not exist anywhere as a formula.
    """
    protocol = " ".join(record.section(name) for name in DEFINING_SECTIONS)
    out: list[tuple[str, str]] = []
    for token, where in sorted(metric_mentions(record).items()):
        if token in protocol:
            continue
        out.append((token, ", ".join(where)))
    return out


def unset_markers(record: Record) -> list[tuple[str, str]]:
    """Every `**[UNSET: ...]**` in a record, as (section name, what is needed)."""
    found: list[tuple[str, str]] = []
    for name, text in record.sections.items():
        found += [(name, m.group(1)) for m in UNSET_PATTERN.finditer(text)]
    for key, value in record.front.items():
        if isinstance(value, str):
            found += [(f"front matter `{key}`", m.group(1)) for m in UNSET_PATTERN.finditer(value)]
    return found

KINDS = ("baseline", "hypothesis", "diagnostic", "ablation", "combination")
STATUSES = ("proposed", "approved", "running", "concluded", "abandoned")
VERDICTS = ("supports", "refutes", "inconclusive")
ORIGINS = ("researcher", "agent-diagnostic", "agent-literature")
MODES = ("guided", "auto")

# Front-matter key order for an experiment. Required keys first, so a record stays readable and
# diffs stay minimal when a field changes.
EXPERIMENT_KEYS = (
    "id",
    "branch",
    "mode",
    "goal",
    "parent",
    "parents",
    "kind",
    "status",
    "verdict",
    "origin",
    "expectation",
    "created",
    "concluded",
)
GOAL_KEYS = ("id", "status", "primary_metric", "direction", "target", "gate_metric")
PROPOSAL_KEYS = (
    "id",
    "goal",
    "origin",
    "proposed_parent",
    "kind",
    "status",
    "proposed_on",
    "decided_on",
)


def validate_id(value: str, what: str = "ID") -> str:
    if not isinstance(value, str) or not ID_PATTERN.fullmatch(value):
        raise HirakuError(
            f"{what} '{value}' must be a lowercase slug: letters, digits, single hyphens."
        )
    return value


def _ordered(front: dict[str, Any], order: tuple[str, ...]) -> dict[str, Any]:
    out = {k: front[k] for k in order if k in front}
    out.update({k: v for k, v in front.items() if k not in out})
    return out


@dataclass
class Record:
    """A markdown record: front matter, named sections, and the path it came from."""

    path: Path
    front: dict[str, Any]
    sections: dict[str, str] = field(default_factory=dict)
    preamble: str = ""

    @property
    def id(self) -> str:
        return str(self.front.get("id") or self.path.stem)

    def section(self, *names: str) -> str:
        for name in names:
            for key, value in self.sections.items():
                if key.lower().startswith(name.lower()):
                    return value
        return ""

    def set_section(self, name: str, text: str, *, before: str | None = None) -> None:
        """Replace a section, or create it. `before` places a new section ahead of that one."""
        for key in self.sections:
            if key.lower().startswith(name.lower()):
                self.sections[key] = text.rstrip() + "\n"
                return
        body = text.rstrip() + "\n"
        if before:
            for key in self.sections:
                if key.lower().startswith(before.lower()):
                    rebuilt: dict[str, str] = {}
                    for existing, value in self.sections.items():
                        if existing == key:
                            rebuilt[name] = body
                        rebuilt[existing] = value
                    self.sections = rebuilt
                    return
        self.sections[name] = body

    def append_section(self, name: str, text: str) -> None:
        existing = self.section(name).strip()
        merged = f"{existing}\n\n{text.strip()}" if existing else text.strip()
        self.set_section(name, merged)

    def body(self) -> str:
        parts = [self.preamble.rstrip()] if self.preamble.strip() else []
        for name, text in self.sections.items():
            parts.append(f"## {name}\n\n{text.rstrip()}")
        return "\n\n".join(parts) + "\n"

    def render(self, order: tuple[str, ...] = ()) -> str:
        return yamlio.join_front_matter(_ordered(self.front, order), self.body())


def _split_sections(body: str) -> tuple[str, dict[str, str]]:
    preamble: list[str] = []
    sections: dict[str, str] = {}
    current: str | None = None
    buffer: list[str] = []
    for line in body.splitlines():
        match = re.match(r"^##\s+(.*)$", line)
        if match:
            if current is None:
                pass
            else:
                sections[current] = "\n".join(buffer).strip() + "\n"
            current = match.group(1).strip()
            buffer = []
        elif current is None:
            preamble.append(line)
        else:
            buffer.append(line)
    if current is not None:
        sections[current] = "\n".join(buffer).strip() + "\n"
    return "\n".join(preamble).strip(), sections


def read_record(path: Path) -> Record:
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as exc:
        raise HirakuError(f"Could not read {path}: {exc}") from exc
    front, body = yamlio.split_front_matter(text)
    preamble, sections = _split_sections(body)
    return Record(path=path, front=front, sections=sections, preamble=preamble)


def write_record(record: Record, order: tuple[str, ...] = ()) -> Path:
    record.path.parent.mkdir(parents=True, exist_ok=True)
    record.path.write_text(record.render(order), encoding="utf-8")
    return record.path


# --------------------------------------------------------------------------- typed accessors


def experiment_path(project: Project, experiment_id: str) -> Path:
    return project.experiments_dir / experiment_id / "experiment.md"


def goal_path(project: Project, goal_id: str) -> Path:
    return project.goals_dir / f"{goal_id}.md"


def proposal_path(project: Project, proposal_id: str) -> Path:
    return project.proposals_dir / f"{proposal_id}.md"


def load_experiment(project: Project, experiment_id: str) -> Record:
    path = experiment_path(project, validate_id(experiment_id, "Experiment ID"))
    if not path.is_file():
        raise HirakuError(f"Experiment '{experiment_id}' does not exist ({project.rel(path)}).")
    record = read_record(path)
    declared = record.front.get("id")
    if declared and declared != experiment_id:
        raise HirakuError(
            f"Record id '{declared}' disagrees with directory '{experiment_id}' in {path}."
        )
    return record


def load_experiments(project: Project) -> dict[str, Record]:
    out: dict[str, Record] = {}
    if not project.experiments_dir.exists():
        return out
    for path in sorted(project.experiments_dir.glob("*/experiment.md")):
        record = read_record(path)
        key = str(record.front.get("id") or path.parent.name)
        if key in out:
            raise HirakuError(
                f"Two records claim id '{key}': {project.rel(out[key].path)} and "
                f"{project.rel(path)}."
            )
        out[key] = record
    return out


def load_goal(project: Project, goal_id: str) -> Record:
    path = goal_path(project, validate_id(goal_id, "Goal ID"))
    if not path.is_file():
        raise HirakuError(f"Goal '{goal_id}' does not exist ({project.rel(path)}).")
    return read_record(path)


def load_goals(project: Project) -> dict[str, Record]:
    out: dict[str, Record] = {}
    if not project.goals_dir.exists():
        return out
    for path in sorted(project.goals_dir.glob("*.md")):
        record = read_record(path)
        out[str(record.front.get("id") or path.stem)] = record
    return out


def load_proposals(project: Project) -> dict[str, Record]:
    out: dict[str, Record] = {}
    if not project.proposals_dir.exists():
        return out
    for path in sorted(project.proposals_dir.glob("*.md")):
        record = read_record(path)
        out[str(record.front.get("id") or path.stem)] = record
    return out


def save_experiment(record: Record) -> Path:
    return write_record(record, EXPERIMENT_KEYS)


def save_goal(record: Record) -> Path:
    return write_record(record, GOAL_KEYS)


def save_proposal(record: Record) -> Path:
    return write_record(record, PROPOSAL_KEYS)
