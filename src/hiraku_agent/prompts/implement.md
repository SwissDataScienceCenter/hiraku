{{rules}}

---

**Exception to the rule above.** For every other workflow, you do not write files —
here, you do. `focus_node` below is a `proposed` node that a person has already
approved (`hiraku-agent` will not call you for this workflow otherwise). Your job is to
write the real code that proposal describes, commit it, and only then return the one
JSON object this prompt asks for. Every other rule above still applies in full: never
invent a number, never touch an existing run row or another node's history, use only
the metric names the task declares, and use the `**[UNSET: ...]**` marker rather than
guessing at anything you cannot ground. The JSON you return — not the diff itself — is
the only thing `hiraku_agent` reads back, so `notes` is where a reviewer later learns
what you did and why, alongside `git log`/`git diff` on the commit you name.

# Task: implement an approved proposal

`focus_node` is the node to implement. Its `expectation`, its `Scope` section, and the
task's `metrics` and existing `entrypoints` describe what you are building and how it
will be judged — you are not choosing the hypothesis, only making it testable.

Do this in order:

1. **Check preconditions.** If `focus_node` already has a `commit`, or is `abandoned`,
   or its `expectation` is empty, stop: set `"blocked": "reason"` and return nothing
   else. (`hiraku_agent` also checks this before calling you; do not rely on that alone.)
2. **Stay on the branch you were given.** You are running in a separate git worktree,
   already checked out on this node's own branch; the researcher's working tree is
   elsewhere and is not yours to touch. Run `git status --short --branch` to confirm.
   Do not switch branches, create other branches, rebase, reset, amend or force
   anything: commit on top of the branch as it is. If you cannot do the work without
   that, stop: set `"blocked"` and explain what you found.
3. **Read before you write.** Inspect the existing code the task's current
   `entrypoints` already run, and anything `project_context` points at. Match the
   project's own conventions (naming, structure, dependencies already in use) rather
   than introducing a new pattern for one experiment.
4. **Make the smallest focused change** that lets `focus_node`'s expectation actually
   be tested — new or modified source, not a restructuring of unrelated code. Only
   declare a new `entrypoints` entry if none of the task's existing ones already
   describe the invocation this node needs (`docs/state.md` and `Entrypoint` — a
   command argument list plus default arguments).
5. **Verify before committing.** At minimum, run the change (or a short smoke
   invocation of it) and confirm it does not crash. State plainly in `notes` what you
   actually ran to check this — never claim a verification you skipped.
6. **Commit.** One commit, a clear message in English describing what this implements
   and for which node, touching only the files this change needs.
7. **Report.** Return the JSON contract below. `commit` is the full hash of the commit
   you just made (`git rev-parse HEAD` after committing). `hiraku_agent` checks that it
   resolves and that it is new: a commit that already existed before this call (the
   HEAD you started from, or anything older) is rejected, because binding it would
   claim code that this call never wrote. If you could not commit, set `blocked`.

## Current state

```json
{{state}}
```

{{instruction}}

## Output contract

```json
{
  "blocked": "reason, or null",
  "commit": "the full hash of the commit you just made, or null if blocked",
  "entrypoints": {
    "train": {"command": ["uv", "run", "python", "train.py"], "args": {}}
  },
  "files_changed": ["path/relative/to/the/repository/root.py"],
  "notes": "what you implemented, what you verified and how, and anything a reviewer should check — for a human reading this later via git log/diff, not for hiraku_agent to parse further"
}
```

Omit `entrypoints` (or leave it `{}`) when the task's existing entrypoints already
describe the right invocation.
