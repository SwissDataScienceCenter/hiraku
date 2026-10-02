# hiraku + hiraku-agent: end-to-end guide

Everything below is real, working CLI on `feat/poc-agent`, ported onto `hiraku` core's
unified-records model (`main`, release `0.1.1` — `Task`/`Experiment` collapsed into one
`Record` class; see `hiraku/unified-records-port-plan-zou.md` for the port itself) — every
command is either read straight out of `hiraku/src/hiraku/cli.py` or
`hiraku_agent/src/hiraku_agent/cli.py`, or actually run while writing this. Two binaries:
`hiraku` (the deterministic record/tracking layer, your colleagues' code) and
`hiraku-agent` (the LLM-driven scheduler on top of it, this branch's code). Run either with
`uv run hiraku ...` / `uv run hiraku-agent ...` from inside the project, or
`uv tool install .` once to put them on `PATH`.

## 0. Set up a project

```bash
cd your-repo               # must be a git repo (or pass --ignore-git)
uv run hiraku init
```

Creates `hiraku/` (where every record lives — `hiraku/records/`, `hiraku/actions/`,
`hiraku/contexts/`, `hiraku/programs/`, `hiraku/agent-logs/`) and the project config.
`--tracking-backend mlflow` (or `csv`) picks how runs get tracked; default is fine to
start.

## 1. Get a task — two ways

A task is just a root record (`parent_id is None`) — `hiraku` core draws no class
distinction between a task and a node under it any more, only the convention of calling
a root record's `type` field `"task"`. A task owns the evaluation protocol
(`goal`/`question`/`metrics`) every record beneath it inherits unchanged. Everything
downstream (`design`, `discover`, `next`, `loop`) needs one to exist first — there is no
"no task yet" mode for any of them except `draft` itself.

**Manual**, when you already know the shape of it:

```bash
uv run hiraku record create vo-backend --type task \
  --set goal="Improve visual-odometry backend accuracy" \
  --set question="Does a deeper backbone raise val_accuracy?" \
  --set metrics='[{name: val_accuracy, direction: maximize}]' \
  --set entrypoints='{train: {command: [python, train.py]}}'
```

**Agent-drafted**, from a sentence:

```bash
uv run hiraku-agent draft -m "we should look at whether a deeper backbone helps \
  validation accuracy on the vo backend" --task-id vo-backend
```

Same effect as `hiraku record create vo-backend --type task`, including binding a git
branch (default `hiraku/records/vo-backend`) — see it in `hiraku record show vo-backend`.
Two things `draft` does **not** do today: it only fills `metrics` when your instruction
actually names one (with a groundable direction — see the note at the end); and it never
touches `entrypoints`, so run `hiraku record update vo-backend --set entrypoints='{...}'`
yourself before you try to `launch` anything.

Check what got created:

```bash
uv run hiraku record show vo-backend
```

## 2. Build the experiment tree

```bash
uv run hiraku-agent design --task vo-backend
uv run hiraku-agent discover --task vo-backend -m "check the literature on backbone depth"
```

`design` proposes experiments from the task's own state (a baseline first, usually);
`discover` searches for candidate hypotheses (from the literature, or from an idea you
give it in `-m`) and writes both accepted proposals and declined ones (so a future
`discover` doesn't re-propose something already considered). Both write plain, uncommitted
child `hiraku record` entries — inspect with:

```bash
uv run hiraku record tree
uv run hiraku record show baseline
```

(`hiraku record tree`/`list` show the whole project, not just one task's subtree — the old
`hiraku experiment list --task ID` scoping is gone along with the separate `Task`/
`Experiment` classes; for one project this is rarely a problem, but it's worth knowing if
you keep several unrelated tasks side by side.)

## 3. The guided cycle: `next` → act → repeat

This is the manual loop — one command tells you what to do, you do it, you ask again:

```bash
uv run hiraku-agent next --task vo-backend
```

It answers with exactly one of: `design` (no experiments yet), `approve` (proposed, gate
not cleared), `implement` (approved, no code bound), `launch` (implemented, never run),
`analyze` (has runs, no verdict), `triage` (concluded, not yet decided what's next), or
`exhausted` (nothing left). What each resolves to:

```bash
# approve — a person's decision, in guided mode (skip in auto mode, see step 5)
uv run hiraku-agent approve baseline --task vo-backend --by qianqian

# implement — the agent writes the code, commits it, and binds the commit itself.
# Approval is the human trigger; the agent is what writes the code either way (see
# `workflows.run_implement`'s docstring). Review what it wrote before trusting it:
uv run hiraku-agent implement baseline --task vo-backend
git log -1 --stat $(uv run hiraku record show baseline --json | jq -r .values.commit)

# (manual alternative, if you'd rather write the code yourself:)
# git checkout -b exp/baseline hiraku/records/vo-backend
# ... write the code, commit it ...
# uv run hiraku record update baseline --commit $(git rev-parse HEAD)

# launch — run it, tracked
uv run hiraku launch train --record baseline

# analyze — once a run exists
uv run hiraku-agent analyze baseline --task vo-backend

# triage — once a node is concluded but nobody's decided what's next
uv run hiraku-agent triage baseline --task vo-backend
```

Check runs any time:

```bash
uv run hiraku runs --record baseline
uv run hiraku runs --record baseline --sort val_accuracy --descending
```

(`hiraku runs --record ID` lists every run attributed to anything in `ID`'s resolved
tracking source, not only `ID`'s own runs — see `docs/guides/command.md`'s "Launching and
runs" section. `hiraku_agent`'s own scheduler needs the narrower, per-node view instead, so
`hiraku_agent.node.Node.runs` filters this back down by hand — see that method's
docstring.)

Just keep calling `hiraku-agent next` — that's the whole cycle. It stops suggesting
anything once every branch is `completed` (`design`/`discover` again from there, or
`triage` a `concluded` node — any verdict, `supports` included — to decide whether it's
worth going further).

## 4. Reusable call configs: `Action` / `Context`

Every `design`/`discover`/`triage`/`analyze`/`draft`/`implement` call above took its
`--agent-cmd`/`--timeout`/`--context` fresh each time. An `Action` record saves a set of
those as a named, reusable default — created with ordinary `hiraku` CLI, no new command:

```bash
uv run hiraku action create vo-backend/nightly-design \
  --set workflow=design \
  --set agent_cmd="claude -p --allowedTools WebSearch,WebFetch,Read,Glob,Grep" \
  --set timeout=1200 \
  --set context='[docs/architecture.md]'
```

`hiraku action create` takes its argument as `{entity}/{action_id}` — `entity` isn't
settable through `--set` (it's immutable, fixed by the path). `entity` **must equal the
task id** you'll use it with — that's a `hiraku_agent` convention (see
`hiraku/program-action-merge-proposal-zou.md`), not a `hiraku` core rule. `--action` on
`hiraku-agent`, below, takes just the bare `nightly-design` part — it already knows the
entity from `--task`. `workflow` names which of the six commands this Action configures.
Then:

```bash
uv run hiraku-agent design --task vo-backend --action nightly-design
```

No `--agent-cmd`/`--timeout`/`--context` needed — they came from the record. Anything you
*do* pass on the command line still wins over the Action's saved value.

A `Context` record is just a named file list, for when several Actions (or calls) should
share the same background reading:

```bash
uv run hiraku context create lit-review --set paths='[docs/architecture.md, README.md]'
uv run hiraku-agent discover --task vo-backend --context-id lit-review
```

An `Action`'s own `context` field can also just name a `Context` id instead of listing
paths again: `--set context=lit-review`.

## 5. The bounded automatic loop: `Program`

A `Program` names which `Action`s a loop is allowed to call, and (optionally) a budget.
Bind one `Action` per workflow you want it to drive automatically:

```bash
uv run hiraku action create vo-backend/auto-design  --set workflow=design
uv run hiraku action create vo-backend/auto-analyze --set workflow=analyze

uv run hiraku program create nightly-loop \
  --set actions='[auto-design, auto-analyze]' \
  --set max_experiments=10 \
  --set max_runs=50 \
  --set time_budget=2h

uv run hiraku-agent loop --program nightly-loop --task vo-backend
```

What it does, each iteration: asks `suggest_next` (same as `hiraku-agent next`), and if the
answer is one of the workflows an Action is bound to, calls it — otherwise **stops and
tells you why**. `approve`/`launch` always stop the loop; those are a person's decision or
a real command, and no `Program` setting changes that. `implement` is different: it's an
LLM-driven workflow like `design`/`discover`/`analyze`/`triage`, so bind an Action to it
(`--set workflow=implement`) and the loop will write the code for an approved node and
move on by itself — the approval gate it already had to clear is the human checkpoint, not
a second stop inside `loop`. It also stops if a budget
field (`max_experiments`/`max_runs`/`time_budget`) is hit, if a step writes nothing (no
point repeating an unproductive call), or if nothing's left to schedule. No `--max-steps`
and no budget field set at all still caps it at 25 iterations — a `Program` is meant to be
bounded, never silently unbounded.

```bash
uv run hiraku-agent loop --program nightly-loop --task vo-backend --max-steps 5
uv run hiraku-agent loop --program nightly-loop --task vo-backend --json   # full step log
```

In `guided` mode (the default for a node unless you create it with `mode: auto`), the loop
will run one `design` step and then stop citing `approve` — that's correct, not a bug: a
person still has to clear the gate before anything downstream happens. To let it run past
that unattended, the nodes it creates need `mode: auto` — `design`'s prompt doesn't set
this today, so for now that means editing the node afterward
(`hiraku record update <id> --set mode=auto`) or accepting that `loop` in guided mode
does one honest step at a time between check-ins.

## Worked example, start to finish

```bash
uv run hiraku init

uv run hiraku-agent draft -m "investigate whether a deeper backbone raises validation \
  accuracy on the vo backend" --task-id vo-backend
uv run hiraku record update vo-backend --set entrypoints='{train: {command: [python, train.py]}}'

uv run hiraku-agent design --task vo-backend
uv run hiraku-agent next --task vo-backend
# -> approve baseline
uv run hiraku-agent approve baseline --task vo-backend --by qianqian

# the agent writes the code, commits it, and binds the commit itself:
uv run hiraku-agent implement baseline --task vo-backend
uv run hiraku launch train --record baseline
uv run hiraku-agent next --task vo-backend
# -> analyze baseline
uv run hiraku-agent analyze baseline --task vo-backend
uv run hiraku-agent next --task vo-backend
# -> triage baseline (whichever way the verdict went — see docs/state.md for why a
#    "supports" result still lands here instead of ending the branch by itself)
uv run hiraku-agent triage baseline --task vo-backend

# once you trust the shape of it, save it as a reusable config and let a bounded
# loop take the next round — including implement and triage, neither of which need a
# person to trigger them each time any more, only to have approved the node once:
uv run hiraku action create vo-backend/auto-design    --set workflow=design
uv run hiraku action create vo-backend/auto-implement --set workflow=implement
uv run hiraku action create vo-backend/auto-triage    --set workflow=triage
uv run hiraku program create solo-loop \
  --set actions='[auto-design, auto-implement, auto-triage]' --set max_experiments=5
uv run hiraku-agent loop --program solo-loop --task vo-backend
```

## What this does not do (yet)

- `draft` has no `--revise`, no `history`, and never proposes `entrypoints` — see the
  `hiraku-agent draft` vs. the old `hiraku_auto` goal-draft comparison earlier in this
  conversation for the full list of gaps.
- `loop` never calls `discover` on its own, because `schedule.suggest_next` never
  suggests it (only `design`, for an empty task) — bind it on a `Program` anyway if a
  future scheduling policy starts suggesting it.
- `loop` cannot clear the approval gate or launch anything — those are a person's decision
  and a real command, deliberately left to a person, exactly like guided mode already
  works today. It *can* drive `implement` once a node is approved — the agent always wrote
  the code, this just lets the loop trigger that step instead of a person calling
  `hiraku-agent implement` by hand each time.
- `Action`/`Context`/`Program` are still plain `hiraku.agent` records carrying
  `hiraku_agent`-only extra fields (`workflow`, `agent_cmd`, `context`, `actions`,
  `max_experiments`, ...) — nothing has been proposed to `hiraku` core to make these
  typed fields yet (Phase 2 in the proposal doc). `hiraku action show`/`hiraku action
  edit` etc. all work on them today exactly like any other record.
