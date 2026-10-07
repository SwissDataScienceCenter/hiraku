"""Delete a record together with everything beneath it: `hiraku-agent delete ID`.

`hiraku record delete` removes one leaf at a time, so starting a task over meant
deleting every experiment by hand, children first. This walks the subtree (a task, or
any node with its descendants) and deletes it bottom-up through the same
`Record.delete` that `hiraku record delete` uses, so every core guard still applies.

Everything is checked before anything is deleted, so a refusal never leaves half a tree:

- A record *outside* the subtree that names one inside it in `related_to` blocks the
  deletion outright: removing it would leave a dangling reference.
- A record with **runs** or a **commit** is evidence, not a draft. It is only deleted
  with `force=True`, and even then its runs stay in the tracking source (they become
  unattributed rows) and its commit stays in Git history.

Also cleared: the active record if it pointed into the subtree, and any `running`
marker. Git branches are never deleted here; existing ones are reported so you can
remove them yourself with `git branch -D`.
"""

from __future__ import annotations

from collections import defaultdict
from contextlib import suppress
from dataclasses import dataclass, field
from graphlib import CycleError, TopologicalSorter

from hiraku.config import HirakuConfig
from hiraku.errors import HirakuError
from hiraku.git import GitRepository
from hiraku.record import Record, list_records

from .errors import AgentError
from .launches import mark_done


@dataclass
class DeletePlan:
    root: str
    records: list[Record] = field(default_factory=list)  # children before parents
    blockers: list[str] = field(default_factory=list)  # refuse, even with force
    evidence: list[str] = field(default_factory=list)  # refuse unless force
    branches: list[str] = field(default_factory=list)  # reported, never deleted

    @property
    def ids(self) -> list[str]:
        return [record.id for record in self.records]


def _runs_by_record(records: list[Record], config: HirakuConfig) -> dict[str, int]:
    counts: dict[str, int] = defaultdict(int)
    if records:
        for run in records[0].runs(config=config):
            counts[run.record_id] += 1
    return counts


def _existing_branches(records: list[Record], config: HirakuConfig) -> list[str]:
    try:
        repository = GitRepository(config)
    except HirakuError:
        return []
    found: list[str] = []
    for record in records:
        candidates = [record.branch] if record.branch else []
        with suppress(HirakuError, KeyError, AttributeError, ValueError):
            candidates.append(repository.record_branch_name(record.id))
        for branch in candidates:
            with suppress(HirakuError):
                if branch not in found and repository.branch_exists(branch):
                    found.append(branch)
    return found


def plan_delete(record_id: str, *, config: HirakuConfig) -> DeletePlan:
    """What deleting `record_id` and its subtree would remove, and what stands in the
    way. Reads only; changes nothing."""
    records = list_records(config=config)
    by_id = {record.id: record for record in records}
    if record_id not in by_id:
        raise AgentError(f"No record {record_id!r}.")
    children: dict[str, list[str]] = defaultdict(list)
    for record in records:
        if record.parent_id:
            children[record.parent_id].append(record.id)

    inside: set[str] = set()
    stack = [record_id]
    while stack:
        identifier = stack.pop()
        if identifier not in inside:
            inside.add(identifier)
            stack.extend(children.get(identifier, []))

    # Delete a record only after everything that would stop `Record.delete` is gone:
    # its children, and any record in the tree that names it in `related_to`.
    before: dict[str, set[str]] = {identifier: set() for identifier in inside}
    for identifier in inside:
        before[identifier].update(children.get(identifier, []))
        for other in inside:
            if other != identifier and identifier in by_id[other].related_to:
                before[identifier].add(other)
    blockers: list[str] = []
    try:
        order = list(TopologicalSorter(before).static_order())
    except CycleError as exc:
        order = sorted(inside)
        blockers.append(
            f"records inside the tree refer to each other in a cycle ({exc})"
        )
    ordered = [by_id[identifier] for identifier in order]
    plan = DeletePlan(root=record_id, records=ordered, blockers=blockers)

    for record in records:
        if record.id in inside:
            continue
        named = sorted(inside & set(record.related_to))
        if named:
            plan.blockers.append(
                f"{record.id} (outside this tree) refers to {', '.join(named)} in "
                "related_to"
            )

    try:
        runs = _runs_by_record(records, config)
    except Exception as exc:  # noqa: BLE001 - any backend failure means "unknown"
        plan.evidence.append(
            f"could not read the tracking source to check for runs ({exc})"
        )
        runs = {}
    for record in ordered:
        if runs.get(record.id):
            plan.evidence.append(f"{record.id} has {runs[record.id]} run(s)")
        if record.commit:
            plan.evidence.append(f"{record.id} is bound to commit {record.commit[:12]}")
    plan.branches = _existing_branches(ordered, config)
    return plan


def refusal(plan: DeletePlan, *, force: bool = False) -> str | None:
    """Why `plan` may not be carried out, or None."""
    if plan.blockers:
        return f"Cannot delete {plan.root!r}: " + "; ".join(plan.blockers) + "."
    if plan.evidence and not force:
        return (
            f"{plan.root!r} has evidence that deleting would orphan: "
            + "; ".join(plan.evidence)
            + ". Re-run with --force if that is really what you want (runs stay in "
            "the tracking source, commits stay in Git)."
        )
    return None


def delete_subtree(
    plan: DeletePlan, *, force: bool = False, config: HirakuConfig
) -> list[str]:
    """Carry out `plan`. Returns the deleted ids, children first."""
    reason = refusal(plan, force=force)
    if reason:
        raise AgentError(reason)
    deleted: list[str] = []
    for record in plan.records:
        directory = record.path.parent
        record.delete(config=config)
        deleted.append(record.id)
        with suppress(OSError):
            directory.rmdir()  # a record's own directory, once it is empty
        mark_done(record.id, config=config)
    if config.active_record_id in set(deleted):
        config.clear_active_record()
    return deleted


def describe(plan: DeletePlan) -> list[str]:
    lines = [f"would delete {len(plan.records)} record(s), children first:"]
    lines += [f"  {record.id}  ({record.path})" for record in plan.records]
    lines += [f"blocked: {item}" for item in plan.blockers]
    lines += [f"evidence: {item}" for item in plan.evidence]
    if plan.branches:
        lines.append(
            "git branches are kept (remove with `git branch -D` if you want): "
            + ", ".join(plan.branches)
        )
    return lines
