"""Generating the markdown records.

This is the code that writes the files the design documents describe. Templates are shipped as
package data and copied into a project by `init`, so a project can edit its own copies without
affecting anything else; rendering substitutes `{{key}}` placeholders and drops the lines of any
placeholder left empty, so a record never carries a dangling `parent:` or `{{goal}}`.
"""

from __future__ import annotations

import datetime as _dt
import re
import shutil
from dataclasses import dataclass
from dataclasses import field as dataclass_field
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


def tool_repo_root(start: Path) -> Path | None:
    """The hiraku-auto source checkout containing `start`, if any.

    A research project must not be created inside the tool's own repository: `init` would drop
    `research/`, `.hiraku/` and `.agents/` into the source tree, and the tool's `.gitignore` and
    git history would start tracking research state.
    """
    for directory in [start, *start.parents]:
        if (directory / "src" / "hiraku_auto" / "cli.py").is_file():
            return directory
    return None


def init_project(
    root: Path,
    *,
    name: str | None = None,
    force: bool = False,
    git: bool = False,
    allow_in_tool_repo: bool = False,
) -> list[Path]:
    """Create a project: config, directories, templates, an empty run table, and the skill."""
    root = Path(root).resolve()

    if not allow_in_tool_repo:
        tool_root = tool_repo_root(root)
        if tool_root is not None:
            suggested = tool_root.parent / f"{tool_root.name}-research"
            raise HirakuError(
                f"{root} is inside the hiraku-auto source repository ({tool_root}).\n"
                f"A research project belongs in its own repository, or `init` would write "
                f"research/ and .hiraku/ into the tool's source tree.\n\n"
                f"  hiraku-auto init {suggested} --git --name '<project name>'\n\n"
                f"To look at the bundled projects instead:\n"
                f"  hiraku-auto --project {tool_root}/examples/worked-tree tree\n\n"
                f"Pass --allow-in-tool-repo only if you really mean to."
            )

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
    if _ensure_gitignore(root):
        written.append(root / ".gitignore")
    if git:
        from . import gitops

        if not gitops.is_repo(root):
            gitops.run_git(root, "init", "-q")
    return written


GITIGNORE_ENTRIES = (
    "# hiraku-auto: machine state, never committed",
    ".worktrees/",
    ".hiraku/agent-logs/",
    ".venv",
    ".venv-*",
    "experiments/*/.venv",
    "experiments/*/.venv-*",
    "__pycache__/",
    "*.py[cod]",
    "*.egg-info/",
    ".pytest_cache/",
    ".ruff_cache/",
    ".mypy_cache/",
    ".ipynb_checkpoints/",
    ".DS_Store",
)


def _ensure_gitignore(root: Path) -> bool:
    """Append the entries that keep environments, caches and build output out of the repository.

    A virtual environment has absolute paths baked in, so committing one produces a copy that is
    broken at any other path — including the temporary worktree a `--commit` run uses. Bytecode
    caches are worse than useless in history: they are regenerated on every import, so they turn
    every commit into a diff of binary files that nobody reads and that conflict on every merge.
    Appends only what is missing, and never rewrites existing lines.
    """
    path = root / ".gitignore"
    existing = path.read_text(encoding="utf-8").splitlines() if path.is_file() else []
    missing = [e for e in GITIGNORE_ENTRIES if e not in existing]
    if not missing or missing == [GITIGNORE_ENTRIES[0]]:
        return False
    body = "\n".join(missing)
    with path.open("a", encoding="utf-8") as stream:
        if existing and existing[-1].strip():
            stream.write("\n")
        stream.write(body + "\n")
    return True


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


PROTOCOL_PARTS: tuple[tuple[str, str], ...] = (
    ("dataset", "Dataset and version"),
    ("splits", "Splits"),
    ("metrics", "Metrics"),
    ("seeds", "Seeds"),
    ("held_fixed", "Held fixed"),
)

PROTOCOL_PREAMBLE = (
    "Fixed **before** any run, and inherited unchanged by every experiment under this goal. An "
    "experiment needing a different protocol is answering a different question and belongs under a "
    "different goal.\n"
)


def _protocol_markdown(protocol: dict[str, Any]) -> str:
    """The five-part protocol, always all five, with a marker where a part is missing.

    A protocol that quietly omits `seeds` looks complete. One that says `**[UNSET: seeds]**` in the
    same place cannot be mistaken for a decision anybody made.
    """
    lines = [PROTOCOL_PREAMBLE]
    for key, label in PROTOCOL_PARTS:
        value = str(protocol.get(key) or "").strip()
        if not value:
            value = f"**[UNSET: {label.lower()} — the agent returned nothing for this]**"
        lines.append(f"### {label}\n\n{value}\n")
    lines.append(
        "Changing anything here invalidates every comparison already made under this goal."
    )
    return "\n".join(lines)


def apply_goal_draft(
    project: Project,
    goal_id: str,
    data: dict[str, Any],
    *,
    revise: bool = False,
) -> Path:
    """Write a goal record from a `draft` reply.

    Creating is the normal path. `revise` rewrites the drafted sections of an existing goal while
    keeping its History, so a second draft after filling in some UNSET markers is not a data loss.
    """
    validate_id(goal_id, "Goal ID")
    path = goal_path(project, goal_id)
    direction = str(data.get("direction") or "minimize")
    if direction not in {"minimize", "maximize"}:
        direction = "minimize"

    created = not path.exists()
    if not created:
        if not revise:
            raise HirakuError(
                f"Goal '{goal_id}' already exists ({project.rel(path)}). Pass --revise to rewrite "
                f"its drafted sections, or draft under a different id."
            )
        record = read_record(path)
        record.append_section("History", f"- {today()} — goal redrafted from the question.")
    else:
        create_goal(
            project,
            goal_id,
            primary_metric=str(data.get("primary_metric") or "metric"),
            direction=direction,
            target=data.get("target"),
            gate_metric=data.get("gate_metric") or None,
            title=str(data.get("title") or "") or None,
        )
        record = read_record(path)

    record.front["primary_metric"] = str(data.get("primary_metric") or "metric")
    record.front["direction"] = direction
    if data.get("target") is not None:
        record.front["target"] = data["target"]
    if data.get("gate_metric"):
        record.front["gate_metric"] = str(data["gate_metric"])

    for key, section in (
        ("question", "Question"),
        ("why_it_matters", "Why it matters"),
        ("success_criterion", "Success criterion"),
        ("out_of_scope", "Out of scope"),
        ("open_threads", "Open threads"),
    ):
        value = str(data.get(key) or "").strip()
        if value:
            record.set_section(section, value)

    protocol = data.get("evaluation_protocol")
    if isinstance(protocol, dict):
        record.set_section("Evaluation protocol", _protocol_markdown(protocol))

    if created:
        record.append_section(
            "History", f"- {today()} — drafted by the agent from the researcher's question."
        )

    grounding = data.get("grounding")
    if isinstance(grounding, list) and grounding:
        lines = ["**Drafted from the project's own files.**", ""]
        for item in grounding:
            if isinstance(item, dict) and item.get("claim"):
                lines.append(f"- {item['claim']} — `{item.get('source', 'unattributed')}`")
        record.append_section("History", "\n".join(lines))

    write_record(record, GOAL_KEYS)
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


# --------------------------------------------------------------------------- removal


@dataclass
class Removal:
    """What a delete took out, and what it deliberately left behind."""

    paths: list[Path] = dataclass_field(default_factory=list)
    left_behind: list[str] = dataclass_field(default_factory=list)


def _members(project: Project, goal_id: str) -> list[str]:
    """Every experiment under a goal, including ones that inherit it through a parent."""
    from .tree import build

    return sorted(build(project).subtree_all(goal_id))


def _descendants(project: Project, experiment_id: str) -> list[str]:
    from .tree import build

    forest = build(project)
    if experiment_id not in forest.nodes:
        raise HirakuError(f"Experiment '{experiment_id}' does not exist.")
    out: list[str] = []
    frontier = [experiment_id]
    while frontier:
        current = frontier.pop()
        out.append(current)
        frontier += forest.nodes[current].children
    return sorted(set(out))


def _run_rows(project: Project, experiment_ids: set[str]) -> int:
    from .runs import read_runs

    table = read_runs(project.table_path)
    return sum(1 for r in table.rows if r.get("experiment_id") in experiment_ids)


def _drop_experiment_dirs(project: Project, ids: list[str], removal: Removal) -> None:
    """Delete the records. Branches, worktrees and run rows are never touched here.

    A branch may hold committed work and the run table is append-only history; destroying either
    on a `rm` would make the command unsafe to reach for. What is left behind is reported instead,
    with the command to finish the job.
    """
    from . import gitops

    for experiment_id in ids:
        directory = project.experiments_dir / experiment_id
        record_path = experiment_path(project, experiment_id)
        # Read the declared branch before the record goes: a renamed branch is still the one
        # holding the work, and guessing `exp/<id>` would name the wrong ref.
        branch = f"exp/{experiment_id}"
        if record_path.is_file():
            branch = str(read_record(record_path).front.get("branch") or branch)
        if directory.is_dir():
            shutil.rmtree(directory)
            removal.paths.append(directory)
        worktree = project.root / ".worktrees" / experiment_id
        if worktree.exists():
            removal.left_behind.append(
                f"worktree {project.rel(worktree)} — "
                f"hiraku-auto worktree remove {experiment_id}"
            )
        if gitops.is_repo(project.root) and gitops.branch_exists(project.root, branch):
            removal.left_behind.append(f"branch {branch} — git branch -D {branch}")


def remove_experiment(
    project: Project,
    experiment_id: str,
    *,
    with_children: bool = False,
    force: bool = False,
) -> Removal:
    """Delete an experiment record, and optionally the subtree under it."""
    validate_id(experiment_id, "Experiment ID")
    ids = _descendants(project, experiment_id)
    children = [i for i in ids if i != experiment_id]
    if children and not with_children:
        raise HirakuError(
            f"'{experiment_id}' has {len(children)} descendant(s): {', '.join(children)}.\n"
            f"Deleting it alone would leave them pointing at a parent that no longer exists. "
            f"Pass --with-children to delete the subtree, or re-parent them first with "
            f"`hiraku-auto exp set <id> --parent <other>`."
        )
    target = set(ids) if with_children else {experiment_id}
    rows = _run_rows(project, target)
    if rows and not force:
        raise HirakuError(
            f"{rows} recorded run(s) belong to {'these experiments' if with_children else 'it'}. "
            f"The run table is append-only history and is never rewritten, so those rows would be "
            f"left pointing at records that no longer exist, and `check` would report them "
            f"forever.\nSet `status: abandoned` instead (`hiraku-auto exp set {experiment_id} "
            f"--status abandoned`) to keep the result and stop the loop touching it, or pass "
            f"--force if you really mean to delete a measured experiment."
        )
    removal = Removal()
    _drop_experiment_dirs(project, sorted(target, reverse=True), removal)
    sync_children(project)
    return removal


def remove_goal(
    project: Project,
    goal_id: str,
    *,
    with_experiments: bool = False,
    force: bool = False,
) -> Removal:
    """Delete a goal record, its pending proposals, and optionally its experiments."""
    path = goal_path(project, validate_id(goal_id, "Goal ID"))
    if not path.is_file():
        raise HirakuError(f"Goal '{goal_id}' does not exist ({project.rel(path)}).")

    from .records import load_proposals

    members = _members(project, goal_id)
    if members and not with_experiments:
        raise HirakuError(
            f"Goal '{goal_id}' still has {len(members)} experiment(s): {', '.join(members)}.\n"
            f"Every comparison they record is only meaningful under this goal's evaluation "
            f"protocol, so removing the goal from under them would strand them.\n"
            f"Move them (`hiraku-auto exp set <id> --goal <other>`), delete them "
            f"(`hiraku-auto exp rm <id>`), or pass --with-experiments to delete them with the goal."
        )
    rows = _run_rows(project, set(members))
    if rows and not force:
        raise HirakuError(
            f"{rows} recorded run(s) belong to this goal's experiments. The run table is "
            f"append-only history and is never rewritten, so those rows would be left pointing at "
            f"records that no longer exist.\nSet `status: abandoned` in "
            f"{project.rel(path)} instead — that keeps the record and its results while taking the "
            f"goal out of the loop — or pass --force if you really mean to delete measured work."
        )

    removal = Removal()
    if with_experiments:
        _drop_experiment_dirs(project, sorted(members, reverse=True), removal)
    for record in load_proposals(project).values():
        if record.front.get("goal") == goal_id:
            record.path.unlink()
            removal.paths.append(record.path)
    path.unlink()
    removal.paths.append(path)
    sync_children(project)
    return removal


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
