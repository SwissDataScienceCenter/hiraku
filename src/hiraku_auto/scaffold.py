"""Generating the markdown records.

This is the code that writes the files the design documents describe. Templates are shipped as
package data and copied into a project by `init`, so a project can edit its own copies without
affecting anything else; rendering substitutes `{{key}}` placeholders and drops the lines of any
placeholder left empty, so a record never carries a dangling `parent:` or `{{goal}}`.
"""

from __future__ import annotations

import datetime as _dt
import re
from pathlib import Path
from typing import Any

from . import yamlio
from .config import CONFIG_NAME, DEFAULT_CONFIG, Project
from .errors import HirakuError
from .records import (
    EXPERIMENT_KEYS,
    GOAL_KEYS,
    PROPOSAL_KEYS,
    Record,
    experiment_path,
    goal_path,
    proposal_path,
    read_record,
    validate_id,
    write_record,
)
from .runs import PROVENANCE_COLUMNS

PACKAGE_TEMPLATES = Path(__file__).parent / "templates"
PLACEHOLDER = re.compile(r"\{\{(\w+)\}\}")


def today() -> str:
    return _dt.date.today().isoformat()


def title_from_id(identifier: str) -> str:
    return identifier.replace("-", " ").capitalize()


def template_dir(project: Project | None) -> Path:
    """A project's own templates win over the packaged ones."""
    if project is not None:
        local = project.root / ".hiraku" / "templates"
        if local.is_dir():
            return local
    return PACKAGE_TEMPLATES


def render(template: str, values: dict[str, Any]) -> str:
    """Substitute `{{key}}`. A line whose only placeholder resolves to empty is dropped from front
    matter (so `parent:` disappears rather than sitting empty), and left blank elsewhere."""
    def substitute(match: re.Match[str]) -> str:
        value = values.get(match.group(1))
        return "" if value is None else str(value)

    out_lines: list[str] = []
    in_front = template.startswith("---")
    seen_delimiters = 0
    for line in template.splitlines():
        if line.strip() == "---":
            seen_delimiters += 1
            out_lines.append(line)
            continue
        rendered = PLACEHOLDER.sub(substitute, line)
        front_matter_line = in_front and seen_delimiters == 1
        if front_matter_line and PLACEHOLDER.search(line):
            key, _, value = rendered.partition(":")
            if not value.strip():
                # An unfilled optional key: omit the line entirely.
                if key.strip() not in {"verdict", "concluded", "decided_on", "target"}:
                    continue
        out_lines.append(rendered)
    return "\n".join(out_lines).rstrip() + "\n"


def _load_template(project: Project | None, name: str) -> str:
    path = template_dir(project) / name
    if not path.is_file():
        path = PACKAGE_TEMPLATES / name
    if not path.is_file():
        raise HirakuError(f"Template '{name}' not found.")
    return path.read_text(encoding="utf-8")


# --------------------------------------------------------------------------- init


def init_project(
    root: Path,
    *,
    name: str | None = None,
    force: bool = False,
) -> list[Path]:
    """Create a project: config, directories, templates, an empty run table, and the skill."""
    root = Path(root).resolve()
    config_path = root / CONFIG_NAME
    if config_path.exists() and not force:
        raise HirakuError(f"{config_path} already exists. Use --force to add missing files only.")

    written: list[Path] = []
    config = dict(DEFAULT_CONFIG)
    project = Project(root=root, raw=config)

    def write(path: Path, text: str) -> None:
        if path.exists() and not force:
            return
        if path.exists():
            return  # --force fills gaps; it never overwrites work
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
        written.append(path)

    write(config_path, yamlio.dump(config))

    for directory in (project.experiments_dir, project.goals_dir, project.proposals_dir):
        directory.mkdir(parents=True, exist_ok=True)

    local_templates = root / ".hiraku" / "templates"
    local_templates.mkdir(parents=True, exist_ok=True)
    for template in sorted(PACKAGE_TEMPLATES.glob("*.md")):
        write(local_templates / template.name, template.read_text(encoding="utf-8"))

    config_md = _load_template(None, "config.md").replace("<project name>", name or root.name)
    write(project.config_md, config_md)
    write(project.leaderboard, _load_template(None, "leaderboard.md"))

    if not project.table_path.exists():
        project.table_path.parent.mkdir(parents=True, exist_ok=True)
        project.table_path.write_text(",".join(PROVENANCE_COLUMNS) + "\n", encoding="utf-8")
        written.append(project.table_path)

    written.extend(install_skill(root, force=force))
    return written


def install_skill(root: Path, *, force: bool = False) -> list[Path]:
    """Copy the agent workflow references into the project so an agent can load them locally."""
    from .prompts import PROMPT_DIR

    target = root / ".agents" / "skills" / "hiraku-auto"
    written: list[Path] = []
    target.mkdir(parents=True, exist_ok=True)
    for source in sorted(PROMPT_DIR.glob("*.md")):
        destination = target / source.name
        if destination.exists() and not force:
            continue
        destination.write_text(source.read_text(encoding="utf-8"), encoding="utf-8")
        written.append(destination)
    return written


# --------------------------------------------------------------------------- records


def create_goal(
    project: Project,
    goal_id: str,
    *,
    primary_metric: str,
    direction: str = "minimize",
    target: Any = None,
    gate_metric: str | None = None,
    question: str = "",
    title: str | None = None,
) -> Path:
    validate_id(goal_id, "Goal ID")
    if direction not in {"minimize", "maximize"}:
        raise HirakuError("direction must be 'minimize' or 'maximize'.")
    path = goal_path(project, goal_id)
    if path.exists():
        raise HirakuError(f"Goal '{goal_id}' already exists ({project.rel(path)}).")
    text = render(
        _load_template(project, "goal.md"),
        {
            "id": goal_id,
            "title": title or title_from_id(goal_id),
            "primary_metric": primary_metric,
            "direction": direction,
            "target": target,
            "gate_metric": gate_metric,
            "question": question
            or "**[State the open research question — falsifiable, about the world.]**",
            "created": today(),
        },
    )
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


def create_experiment(
    project: Project,
    experiment_id: str,
    *,
    goal: str | None = None,
    parent: str | None = None,
    kind: str = "hypothesis",
    mode: str = "guided",
    status: str = "proposed",
    origin: str = "researcher",
    expectation: str = "",
    scope: str = "",
    branch: str | None = None,
    title: str | None = None,
) -> Path:
    validate_id(experiment_id, "Experiment ID")
    path = experiment_path(project, experiment_id)
    if path.exists():
        raise HirakuError(f"Experiment '{experiment_id}' already exists ({project.rel(path)}).")

    if parent:
        parent_path = experiment_path(project, parent)
        if not parent_path.is_file():
            raise HirakuError(f"Parent '{parent}' does not exist.")
        parent_record = read_record(parent_path)
        if goal is None:
            goal = parent_record.front.get("goal")
        parent_mode = str(parent_record.front.get("mode") or "guided")
        if parent_mode == "guided" and mode == "auto":
            raise HirakuError(
                f"'{experiment_id}' cannot be 'auto': its parent '{parent}' is 'guided', and a "
                "child may narrow its parent's mode, never widen it."
            )
    if goal and not goal_path(project, str(goal)).is_file():
        raise HirakuError(f"Goal '{goal}' does not exist. Create it with `hiraku-auto goal add`.")
    if not goal and not parent:
        raise HirakuError("An experiment needs --goal (or a --parent to inherit it from).")

    text = render(
        _load_template(project, "experiment.md"),
        {
            "id": experiment_id,
            "title": title or title_from_id(experiment_id),
            "branch": branch or f"exp/{experiment_id}",
            "mode": mode,
            "goal": goal,
            "parent": parent,
            "kind": kind,
            "status": status,
            "origin": origin,
            "expectation": _quote(expectation) if expectation else '""',
            "created": today(),
            "scope": scope
            or "**[Describe the bounded research question or implementation task.]**",
            "expectation_body": expectation
            or "**[Predicted direction and size of the effect, on which metric, by what "
            "mechanism.]**",
        },
    )
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


def create_proposal(
    project: Project,
    proposal_id: str,
    *,
    goal: str,
    origin: str = "agent-literature",
    proposed_parent: str | None = None,
    kind: str = "hypothesis",
    title: str | None = None,
    hypothesis: str = "",
    provenance: str = "",
    applicability: str = "",
    prediction: str = "",
    investigation_test: str = "",
    cost: str = "",
    counterargument: str = "",
) -> Path:
    validate_id(proposal_id, "Proposal ID")
    path = proposal_path(project, proposal_id)
    if path.exists():
        raise HirakuError(f"Proposal '{proposal_id}' already exists.")
    gap = "**[Not provided.]**"
    text = render(
        _load_template(project, "proposal.md"),
        {
            "id": proposal_id,
            "title": title or title_from_id(proposal_id),
            "goal": goal,
            "origin": origin,
            "proposed_parent": proposed_parent,
            "kind": kind,
            "created": today(),
            "hypothesis": hypothesis or gap,
            "provenance": provenance or gap,
            "applicability": applicability or gap,
            "prediction": prediction or gap,
            "investigation_test": investigation_test or gap,
            "cost": cost or gap,
            "counterargument": counterargument or gap,
        },
    )
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


def _quote(text: str) -> str:
    return '"' + str(text).replace('"', "'").replace("\n", " ").strip() + '"'


def accept_proposal(project: Project, proposal_id: str, *, mode: str = "guided") -> Path:
    """Turn an accepted proposal into an experiment, carrying its origin and parent across."""
    from .records import load_proposals, save_proposal

    proposals = load_proposals(project)
    if proposal_id not in proposals:
        raise HirakuError(f"Proposal '{proposal_id}' does not exist.")
    record = proposals[proposal_id]
    front = record.front
    path = create_experiment(
        project,
        proposal_id,
        goal=front.get("goal"),
        parent=front.get("proposed_parent") or None,
        kind=str(front.get("kind") or "hypothesis"),
        mode=mode,
        status="approved",
        origin=str(front.get("origin") or "agent-literature"),
        expectation=record.section("Hypothesis").strip().splitlines()[0]
        if record.section("Hypothesis").strip()
        else "",
        scope=f"Accepted from proposal `{proposal_id}`.\n\n"
        + record.section("Why it applies here").strip(),
    )
    front["status"] = "accepted"
    front["decided_on"] = today()
    record.append_section("Decision", f"Accepted on {today()}; created experiment `{proposal_id}`.")
    save_proposal(record)
    return path


def sync_children(project: Project) -> list[Path]:
    """Regenerate every record's Children section from the declared parent links."""
    from .tree import build

    forest = build(project)
    touched: list[Path] = []
    for node in forest.nodes.values():
        if node.children:
            lines = []
            for child_id in node.children:
                child = forest.nodes[child_id]
                bits = [b for b in (child.kind, child.status, child.verdict) if b]
                lines.append(f"- `{child_id}` — {', '.join(bits) if bits else 'no metadata'}")
            text = "\n".join(lines)
        else:
            text = "None."
        if node.record.section("Children").strip() != text:
            node.record.set_section("Children", text)
            touched.append(write_record(node.record, EXPERIMENT_KEYS))
    return touched


def set_fields(record: Record, updates: dict[str, Any]) -> Record:
    for key, value in updates.items():
        if value is None:
            record.front.pop(key, None)
        else:
            record.front[key] = value
    return record


__all__ = [
    "init_project",
    "install_skill",
    "create_goal",
    "create_experiment",
    "create_proposal",
    "accept_proposal",
    "sync_children",
    "set_fields",
    "render",
    "today",
    "EXPERIMENT_KEYS",
    "GOAL_KEYS",
    "PROPOSAL_KEYS",
]
