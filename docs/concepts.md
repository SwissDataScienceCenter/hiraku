# Hiraku concepts

In model development, researchers define an idea, implement it, run it, compare
results, and either iterate or conclude. Git records the code, while tracking
systems record executions. Hiraku connects those artifacts to the question,
hypothesis, diagnosis, and decision that motivated them. This makes a research
trail readable by both a person and a coding agent.

The cycle usually has five steps:

1. Define a baseline, hypothesis, or diagnostic.
2. Implement and debug the approach.
3. Execute runs and track metrics.
4. Analyze and compare results.
5. Iterate or conclude, revisiting the broader goal when needed.

Hiraku uses **records** and **runs**. Hiraku manages the records. Runs and metric
logging belong to a user-managed tracking backend; Hiraku queries them through
CSV, MLflow, W&B, or a custom adapter.

| Term | Stored in | Role |
| --- | --- | --- |
| Root record | `hiraku/records/{root_id}/{root_id}.md` | Broad research scope and shared defaults |
| Child record | `hiraku/records/{root_id}/{record_id}.md` | Focused attempt, review, or follow-up |
| Run | User-managed tracking backend | One execution with metrics and provenance |

A record is a Markdown file with YAML front matter. It can describe an overall
research goal, a proposed experiment, a diagnosis, a review, or a conclusion.
The optional `type` field describes its role; no type is required to create or
run a record. A record may bind an implementation commit, define entrypoints,
and carry metric and tracking settings.

Each record has at most one `parent_id`. The parent defines the organizational
tree and inheritance. A root is stored at
`hiraku/records/{root_id}/{root_id}.md`; every descendant is stored at
`hiraku/records/{root_id}/{record_id}.md`, even when the logical tree has more
than two levels. IDs are unique across the project.

A root record often describes a broad goal. Children can represent baselines,
hypotheses, diagnostics, ablations, or follow-up questions; the `type` field is
descriptive rather than a restriction on behavior. Records are ordinary Markdown
files with YAML front matter. You can commit them, review their changes, and
merge them with Git. Hiraku sees the files present in the current checkout;
merging records across branches remains the project's responsibility.

## Inheritance and links

An omitted field inherits from the nearest ancestor that defines it. A local
value wins, and an explicit `null` clears an inherited value. Entrypoints
merge by name: a child may override `train` and still use its parent's
`evaluate` entrypoint. `hiraku record show ID` displays effective values and
their defining records; `--raw` shows the stored file.

`id`, `parent_id`, `related_to`, `type`, `commit`, `status`, and `decision` are
record-local. In particular, a child does not silently claim its parent's
commit or completed decision. `related_to` is a list of record IDs for cross
references. It does not affect inheritance or claim derivation. Explain the
meaning of a link in Markdown when it matters.

## Runs and execution

Runs belong to a tracking backend such as CSV, MLflow, or W&B. Each run
references one `record_id`. `hiraku runs --record ID` returns direct runs;
add `--descendants` to aggregate the subtree. A record's effective tracking
configuration chooses the backend for its runs.

The backend returns normalized run IDs, metrics, and parameters, with optional
commit, timestamp, entrypoint, URL, and other attribution. The training script
calculates and logs results; Hiraku does not own that process. A launch that
never logs a run will not appear in a run query.

Entrypoints declare commands and default arguments. Launch one with
`hiraku launch NAME --record ID`. The dispatcher receives the record ID and
file path, the project root, and entrypoint name. It reports the actual Git
commit and state. `commit` on a record is a local binding to source code; Git
history and `related_to` serve different purposes.

For example, an entrypoint can declare the command and default arguments:

```yaml
entrypoints:
  train:
    command: [uv, run, python, train.py]
    args:
      model: logistic
      seed: 42
```

`hiraku launch train --record baseline -- --seed 1` overrides that default
for one run. The project-owned `hiraku/dispatch/launch.sh` executes the current
working state and reports its HEAD commit and clean or dirty state.
`--dispatch worktree` selects `worktree.sh` to run an exact commit in an
isolated checkout. Projects may add dispatchers such as `runai.sh`; the
dispatcher receives the launch context and the command to execute. The launch
environment includes `HIRAKU_RECORD_ID`, `HIRAKU_RECORD_FILE`, `HIRAKU_ROOT`,
and `HIRAKU_ENTRYPOINT`; dispatchers report `HIRAKU_COMMIT` and
`HIRAKU_GIT_STATE`.

## Active record

The project configuration stores one `active_record_id`. Creation activates
the new record by default. `hiraku record set ID` changes it, and
`hiraku status --json` shows it. Commands that accept `--record` use the
explicit ID instead.

## Command-line and Python interfaces

```bash
hiraku init
hiraku record create study --set 'goal=Improve validation accuracy'
hiraku record create baseline --parent study --type experiment --commit HEAD
hiraku record list
hiraku record tree
hiraku record show baseline
hiraku record entrypoints baseline
hiraku launch train --record baseline
hiraku runs --record study --descendants --sort val/accuracy --descending
```

The [Python API](api/python/index.md) provides the same record, configuration,
launch, and run-query operations. The CLI calls those APIs rather than keeping
a separate set of record rules.

## Agent resources

`hiraku init` installs the Hiraku skill at `.agents/skills/hiraku/` for Codex
and `.claude/skills/hiraku/` for Claude Code. Invoke it as `$hiraku` or
`/hiraku`, respectively. Hiraku also stores project-owned **actions** under
`hiraku/actions/{entity}/{action_id}/`, **programs** under
`hiraku/programs/{program_id}/`, and curated **contexts** under
`hiraku/contexts/{context_id}/`. These resources use the generic Markdown
`BaseRecord` persistence model; they do not participate in research-record
inheritance. The CLI can create, list, and show them:

```bash
hiraku program list
hiraku program show autoresearch
hiraku context create literature
hiraku action list
```

The [Heart Disease](examples/heart-disease/README.md) and
[Autoresearch](examples/autoresearch/README.md) examples show interactive and
agent-assisted workflows.
