"""Deterministic commit and evidence check for experiment implementation."""

import subprocess

from hiraku.agent.action import ActionError, ConditionResult
from hiraku.record import load_record


def _git(context, *arguments, check=True):
    result = subprocess.run(
        ["git", *arguments],
        cwd=context.config.root,
        text=True,
        capture_output=True,
        check=False,
    )
    if check and result.returncode:
        raise ActionError(result.stderr.strip() or "Git command failed")
    return result


def _changed_paths(context, *, exclude_hiraku):
    raw = _git(
        context, "status", "--porcelain", "-z", "--untracked-files=all", "--", "."
    ).stdout.split("\0")
    hiraku = context.config.hiraku_dir.relative_to(context.config.root).as_posix()
    excluded = hiraku if exclude_hiraku else f"{hiraku}/agent-logs"
    paths = []
    index = 0
    while index < len(raw) and raw[index]:
        entry = raw[index]
        status, path = entry[:2], entry[3:]
        if path != excluded and not path.startswith(f"{excluded}/"):
            paths.append(path)
        if "R" in status or "C" in status:
            index += 1
            source = raw[index]
            if source != excluded and not source.startswith(f"{excluded}/"):
                paths.append(source)
        index += 1
    return sorted(set(paths))


def precondition(context):
    if context.record.commit is not None:
        return ConditionResult.fail(
            "Implementation requires a record without a bound commit"
        )
    staged = _git(context, "diff", "--cached", "--quiet", check=False)
    if staged.returncode or _changed_paths(context, exclude_hiraku=False):
        return ConditionResult.fail("Implementation requires a clean starting tree")
    return ConditionResult.pass_()


def after(context, result):
    if _git(context, "rev-parse", "HEAD").stdout.strip() != context.state["head"]:
        raise ActionError("Agent created a commit; Hiraku owns implementation commits")
    record = load_record(context.record.id, config=context.config)
    if (
        record.to_dict() != context.record.to_dict()
        or record.markdown != context.record.markdown
    ):
        raise ActionError("Agent changed the record before validation")
    paths = _changed_paths(context, exclude_hiraku=True)
    if not paths:
        raise ActionError("Implementation produced no changes to commit")
    for path in paths:
        context.report("file_changed", f"Changed {path}")
    _git(context, "add", "--all", "--", *paths)
    staged = _git(context, "diff", "--cached", "--quiet", check=False)
    if staged.returncode == 0:
        raise ActionError("Implementation produced no changes to commit")
    if staged.returncode != 1:
        raise ActionError(staged.stderr.strip() or "Could not inspect staged changes")
    _git(context, "commit", "-m", f"feat: implement {context.record.id}")
    record.commit = _git(context, "rev-parse", "HEAD").stdout.strip()
    context.report("commit_created", f"Created commit {record.commit}")
    record.save()
    context.report("commit_bound", f"Bound {record.id} to {record.commit}")


def before(context):
    context.state["head"] = _git(context, "rev-parse", "HEAD").stdout.strip()


def postcondition(context, result):
    record = load_record(context.record.id, config=context.config)
    if record.commit != _git(context, "rev-parse", "HEAD").stdout.strip():
        return ConditionResult.fail("Implementation requires a bound commit")
    return ConditionResult.pass_()
