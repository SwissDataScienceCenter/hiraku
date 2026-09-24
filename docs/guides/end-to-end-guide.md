# hiraku + hiraku-agent: end-to-end guide

Everything below is real, working CLI on `feat/poc-agent` as of commit `47908a6` — every
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

Creates `hiraku/` (where every record lives — `hiraku/tasks/`, `hiraku/experiments/`,
`hiraku/actions/`, `hiraku/contexts/`, `hiraku/programs/`, `hiraku/agent-logs/`) and the
project config. `--tracking-backend mlflow` (or `csv`) picks how runs get tracked; default
is fine to start.

## 1. Get a task — two ways

A task owns the evaluation protocol (`goal`/`question`/`metrics`) every experiment beneath
it inherits unchanged. Everything downstream (`design`, `discover`, `next`, `loop`) needs
one to exist first — there is no "no task yet" mode for any of them except `draft` itself.

**Manual**, when you already know the shape of it:

```bash
uv run hiraku task create vo-backend \
  --goal "Improve visual-odometry backend accuracy" \
  --question "Does a deeper backbone raise val_accuracy?" \
  --set metrics='[{name: val_accuracy, direction: maximize}]' \
  --set entrypoints='{train: {command: [python, train.py]}}'
```

**Agent-drafted**, from a sentence:

```bash
uv run hiraku-agent draft -m "we should look at whether a deeper backbone helps \
  validation accuracy on the vo backend" --task-id vo-backend
```

Same effect as `hiraku task create`, including binding a git branch (default
`hiraku/tasks/vo-backend`) — see it in `hiraku task show vo-backend`. Two things `draft`
does **not** do today: it only fills `metrics` when your instruction actually names one
(with a groundable direction — see the note at the end); and it never touches
`entrypoints`, so run `hiraku task update vo-backend --set entrypoints='{...}'` yourself
before you try to `launch` anything.

Check what got created:

```bash
uv run hiraku task show vo-backend
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
`hiraku experiment` records — inspect with:

```bash
uv run hiraku experiment list --task vo-backend
uv run hiraku experiment show baseline --task vo-backend
```

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

# implement — bind the commit that actually implements the experiment
git checkout -b exp/baseline hiraku/tasks/vo-backend   # or whatever branch you're using
# ... write the code, commit it ...
uv run hiraku experiment update baseline --task vo-backend --commit $(git rev-parse HEAD)

# launch — run it, tracked
uv run hiraku launch baseline:train --task vo-backend

# analyze — once a run exists
uv run hiraku-agent analyze baseline --task vo-backend

# triage — once a node is concluded but nobody's decided what's next
uv run hiraku-agent triage baseline --task vo-backend
```

Check runs and the leaderboard any time:

```bash
uv run hiraku run list --task vo-backend --experiment baseline
uv run hiraku run list --task vo-backend --sort val_accuracy --descending
```

Just keep calling `hiraku-agent next` — that's the whole cycle. It stops suggesting
anything once every branch has a settled verdict (`design`/`discover` again from there, or
`triage` a `concluded_untriaged` node to decide on children).

## 4. Reusable call configs: `Action` / `Context`

Every `design`/`discover`/`triage`/`analyze`/`draft` call above took its `--agent-cmd`/
`--timeout`/`--context` fresh each time. An `Action` record saves a set of those as a
named, reusable default — created with ordinary `hiraku` CLI, no new command:

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
entity from `--task`. `workflow` names which of the five commands this Action configures.
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
tells you why**. `approve`/`implement`/`launch` always stop the loop; those need a person
or a real commit/command, and no `Program` setting changes that. It also stops if a budget
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
(`hiraku experiment update <id> --set mode=auto`) or accepting that `loop` in guided mode
does one honest step at a time between check-ins.

## Worked example, start to finish

```bash
uv run hiraku init

uv run hiraku-agent draft -m "investigate whether a deeper backbone raises validation \
  accuracy on the vo backend" --task-id vo-backend
uv run hiraku task update vo-backend --set entrypoints='{train: {command: [python, train.py]}}'

uv run hiraku-agent design --task vo-backend
uv run hiraku-agent next --task vo-backend
# -> approve baseline
uv run hiraku-agent approve baseline --task vo-backend --by qianqian

# ... implement it for real, commit, then:
uv run hiraku experiment update baseline --task vo-backend --commit <sha>
uv run hiraku launch baseline:train --task vo-backend
uv run hiraku-agent next --task vo-backend
# -> analyze baseline
uv run hiraku-agent analyze baseline --task vo-backend

# once you trust the shape of it, save it as a reusable config and let a bounded
# loop take the next round:
uv run hiraku action create vo-backend/auto-design --set workflow=design
uv run hiraku program create solo-loop --set actions='[auto-design]' --set max_experiments=5
uv run hiraku-agent loop --program solo-loop --task vo-backend
```

## What this does not do (yet)

- `draft` has no `--revise`, no `history`, and never proposes `entrypoints` — see the
  `hiraku-agent draft` vs. the old `hiraku_auto` goal-draft comparison earlier in this
  conversation for the full list of gaps.
- `loop` never calls `discover` on its own, because `schedule.suggest_next` never
  suggests it (only `design`, for an empty task) — bind it on a `Program` anyway if a
  future scheduling policy starts suggesting it.
- `loop` cannot clear the approval gate, bind a commit, or launch anything — those are
  real decisions and real commands, deliberately left to a person, exactly like guided
  mode already works today.
- `Action`/`Context`/`Program` are still plain `hiraku.agent` records carrying
  `hiraku_agent`-only extra fields (`workflow`, `agent_cmd`, `context`, `actions`,
  `max_experiments`, ...) — nothing has been proposed to `hiraku` core to make these
  typed fields yet (Phase 2 in the proposal doc). `hiraku action show`/`hiraku action
  edit` etc. all work on them today exactly like any other record.
