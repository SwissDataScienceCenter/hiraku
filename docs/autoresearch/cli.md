# Using it

## Install

Nothing is on your PATH until the package is installed. From the repository root:

```bash
uv sync --extra ui           # creates .venv and installs hiraku-auto + Streamlit
uv run hiraku-auto --help
```

`uv run` prefixes every command below. To drop the prefix, activate the environment once:

```bash
source .venv/bin/activate
hiraku-auto --help
```

With pip instead:

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -e '.[ui]'
hiraku-auto --help
```

Without installing anything at all — useful for a quick look at a clone:

```bash
PYTHONPATH=src python3 -m hiraku_auto --project examples/natix-vo tree
```

The package itself has no required dependencies (PyYAML is used when importable, with a fallback
parser for the front-matter subset). The `ui` extra adds Streamlit, which `hiraku-auto ui` needs.

## The tool repo and your research repo are different repositories

`hiraku-auto init` creates a *research project*: `research/`, `experiments/`, `.hiraku/`, `.agents/`.
That belongs in its own repository, not in the tool's source tree — so `init` refuses to run inside
the hiraku-auto checkout and tells you where to put it instead. One command does the whole thing,
including creating the directory and the git repository:

```bash
hiraku-auto init ~/PycharmProjects/natix-research --git --name "NATIX VO"
cd ~/PycharmProjects/natix-research
```

**The installed command works from anywhere.** `pip install -e .` writes an absolute path into
`.venv/bin/hiraku-auto` and into the `.pth` file, so once the environment is on your `PATH` the
command does not care what directory you are in — `cd` into your research repo and use it normally.
Three ways to have it available:

```bash
source ~/PycharmProjects/hiraku/.venv/bin/activate       # per shell session
ln -s ~/PycharmProjects/hiraku/.venv/bin/hiraku-auto ~/.local/bin/   # once, permanently
~/PycharmProjects/hiraku/.venv/bin/hiraku-auto --help    # absolute path, no setup
```

Every command also takes `--project <path>`, so you can drive a project without `cd`-ing into it.

## Ten minutes, end to end

```bash
hiraku-auto init ~/PycharmProjects/natix-research --git --name "NATIX VO"
cd ~/PycharmProjects/natix-research

# Either write the goal yourself...
hiraku-auto goal add vo-backend-selection \
  --metric ate_rmse_m --direction minimize --target 3.0 \
  --gate-metric completion_rate \
  --question "Which VO backend produces usable trajectories on NATIX video?"

# ...or let the agent draft it from your question and the project's own files, then review it.
# Values it cannot ground come back as **[UNSET: ...]** markers instead of guesses.
hiraku-auto context                       # what the agent will be allowed to read
hiraku-auto goal draft vo-backend-selection \
  --question "which VO backend works on our NATIX dashcam video? OccAny is the current one"
hiraku-auto goal show vo-backend-selection   # lists what is still unset

hiraku-auto exp create occany-baseline --goal vo-backend-selection --kind baseline \
  --status approved --expectation "OccAny defines the reference every backend is measured against."

hiraku-auto exp create vggt-omega --parent occany-baseline --status approved \
  --expectation "Global feed-forward pose lowers ate_rmse_m without lowering completion_rate."

hiraku-auto tree
hiraku-auto check
```

`exp create` writes `experiments/<id>/experiment.md`, creates branch `exp/<id>`, and refreshes every
parent's **Children** section. `--parent` is what makes it a child; `--goal` is inherited from the
parent when omitted.

Then declare an entrypoint and run it:

```yaml
# experiments/vggt-omega/launch.yml
entrypoints:
  train:
    command: ["uv", "run", "python", "-m", "vo.run", "--backend", "vggt-omega"]
    cwd: .
```

```bash
git add -A && git commit -m "vggt-omega entrypoint"
hiraku-auto launch vggt-omega:train --commit HEAD --seed 1
hiraku-auto launch vggt-omega:train --commit HEAD --seed 2
hiraku-auto runs vggt-omega --sort ate_rmse_m --ascending
```

### Reporting metrics

The launcher writes the provenance columns itself. Your code reports its numbers by writing one JSON
object to `$HIRAKU_METRICS_PATH`; those keys become columns on the row.

```python
import json, os
json.dump({"ate_rmse_m": 2.41, "completion_rate": 0.93},
          open(os.environ["HIRAKU_METRICS_PATH"], "w"))
```

Also injected: `HIRAKU_EXPERIMENT_ID`, `HIRAKU_GIT_COMMIT`, `HIRAKU_RUN_ID`, `HIRAKU_RUN_KIND`,
`HIRAKU_REPRODUCIBLE`, `HIRAKU_ENTRYPOINT`, `HIRAKU_SEED`, `HIRAKU_TABLE_PATH`,
`HIRAKU_PROJECT_ROOT`.

### Reproducible or not

`--commit <ref>` checks that commit out into a temporary worktree and runs there, leaving your
checkout alone: `run_kind: commit`, `reproducible: true`. Without it the run uses the working tree
including uncommitted changes: `reproducible: false`, and **the promotion gate refuses it** however
good the number.

## Environments

Two different things get called "the environment", and keeping them apart matters:

1. **The tool's environment** — the `.venv` at the repository root that `uv sync` creates. It holds
   `hiraku-auto` and Streamlit and nothing else. The package has no required dependencies, so this
   is tiny and you never touch it again.
2. **Each experiment's environment** — where OccAny, VGGT-Omega and VGGT-Long actually run. These
   need *different, conflicting* libraries: different torch, different CUDA, sometimes different
   Python. This is the one that matters, and it is per experiment, not per project.

`launch` never assumes the second one. Pick the level of isolation the experiment needs:

| Level | How | Isolates |
|---|---|---|
| Inherit the caller | no `venv:` key | nothing — fine for a quick baseline |
| Per-experiment venv | `venv: .venv` in the entrypoint | Python packages |
| Conda / mamba env | `venv: /path/to/conda/envs/vggt` — any directory with a `bin/` works | Python packages and native libs conda manages |
| Container | put it in `command`: `["apptainer", "exec", "vggt.sif", "python", "train.py"]` | the whole userspace, including CUDA userland |
| Separate worktree | `hiraku-auto worktree add <exp>` | the *code*, so siblings can run at once |

A per-experiment venv, which is the common case:

```bash
cd experiments/vggt-omega
uv venv .venv && .venv/bin/pip install -r requirements.txt
cd ../vggt-long
uv venv .venv && .venv/bin/pip install -r requirements.txt   # a different torch, no conflict
```

```yaml
# experiments/vggt-omega/launch.yml
entrypoints:
  train:
    command: ["python", "-m", "vo.run", "--backend", "vggt-omega"]
    cwd: .
    venv: .venv        # resolved in the experiment dir, then the project root
```

`launch` prepends that environment's `bin/` to `PATH` and sets `VIRTUAL_ENV`, so `python` in the
command resolves there. The row records `venv`, the resolved `interpreter`, and
`lockfile_sha256` — read from the **experiment's own** lockfile if it has one, falling back to the
project's.

**A venv is machine state, not commit state.** Its paths are absolute, so a copy at a different path
is broken, and `init` gitignores `.venv` and `experiments/*/.venv*` for that reason. It follows that
`--commit` isolates *code*, not dependencies: the code comes from the commit, the environment comes
from the machine, and `lockfile_sha256` is what records which dependency set ran. If you need the
dependencies pinned to the commit too, use a container and record the image digest as a metric.

### Containers

The command is argv, so a container needs nothing special to *run*. What it does need is a recorded
identity: inside a container, `lockfile_sha256` and `interpreter` describe the launcher's side of
the bind mount, not what the code ran against, so without the image the row's `reproducible: true`
would claim more than it knows. Declare it:

```yaml
entrypoints:
  train:
    command: ["apptainer", "exec", "--nv", "images/vggt.sif", "python", "-m", "vo.run"]
    cwd: .
    container:
      image: images/vggt.sif
```

`container_image` and `container_digest` become columns on the row. A local image file is hashed and
the digest **cached** by (path, size, mtime), so a multi-gigabyte `.sif` is hashed once and a rebuild
invalidates it automatically. For a registry reference, give the command that resolves it — otherwise
only the reference is recorded and `launch` warns that the environment is not pinned:

```yaml
    container:
      image: ghcr.io/lab/vggt:2.1
      digest_cmd: ["docker", "image", "inspect", "--format", "{{.Id}}", "ghcr.io/lab/vggt:2.1"]
```

#### One base image, per-experiment packages

Two ways, and the second is usually the right one.

**Bake an image per experiment.** `base.def` → `vggt-omega.sif`, `vggt-long.sif`. Fully pinned, and
`--commit` then isolates dependencies as well as code. The cost is a rebuild per change and tens of
gigabytes of near-identical images.

**One base image plus an overlay venv per experiment.** The base image carries the heavy, shared
layer — CUDA, torch — and each experiment adds only its own packages, in a venv created with
`--system-site-packages` so it *inherits* from the image instead of duplicating it:

```bash
cd experiments/vggt-omega
apptainer exec ../../images/base.sif python -m venv --system-site-packages .overlay
apptainer exec ../../images/base.sif .overlay/bin/pip install -r requirements.txt
```

```yaml
entrypoints:
  train:
    command: ["apptainer", "exec", "--nv", "../../images/base.sif", "python", "-m", "vo.run"]
    cwd: .
    venv: .overlay          # PATH and VIRTUAL_ENV, passed through into the container
    container:
      image: ../../images/base.sif
```

`launch` sets `VIRTUAL_ENV` and prepends `.overlay/bin` to `PATH` in the *host* process; Apptainer
passes the host environment through and bind-mounts the current directory by default, so the same
absolute path is valid on both sides and `python` inside the container resolves to the overlay. The
row then records both halves — the image digest and the overlay path — which together identify the
environment.

Two caveats worth knowing before you rely on this:

- **Docker does not pass the host environment or mount the cwd by default.** Under Docker the
  passthrough has to be explicit in the command: `-v $PWD:$PWD -w $PWD -e VIRTUAL_ENV -e PATH`.
  Apptainer and Singularity do it for you, which is why they are the easier fit on a GPU cluster.
- **Create the overlay from inside the container**, as above. A venv built by the host Python points
  at the host interpreter, and the ABI will not match the image's.

For the sharpest pinning, do both: an overlay for iteration, and a baked image once a result is worth
promoting.

## Tracking, and MLflow

`.hiraku/runs.csv` is always written, and it is the source of truth. That is not a preference: the
promotion gate compares `run_kind`, `lockfile_sha256`, `container_digest` and the goal record *as it
stood at the run's commit*, and MLflow has no column for any of those. A project whose runs lived
only in MLflow could not evaluate its own gate, and `check` would need a network connection to
answer a question about local files.

So the backend is `table` or `table+mlflow`. There is deliberately no `mlflow` alone.

### Where to configure it

`research/config.md` front matter — the project contract, one place:

```yaml
tracking:
  backend: table+mlflow
  mlflow:
    tracking_uri: http://mlflow.example.org:5000   # empty -> MLFLOW_TRACKING_URI, then ./mlruns
    experiment: null                                # null -> use the goal id
    tags:
      team: sdsc
```

`.hiraku.yml` may also carry a `tracking:` block (it already owns `path`); `research/config.md`
wins key by key, because that is the file a researcher edits.

Two optional overrides:

```yaml
# research/goals/<id>.md — name the MLflow experiment for this goal
mlflow_experiment: natix-crossdomain-2026Q3
```

```yaml
# experiments/<id>/launch.yml — turn the mirror off for one node
tracking:
  backend: table
```

An experiment may **narrow** tracking to `table`; it may never widen it. Same rule as `mode` and the
project bounds — a node that could switch mirroring on for itself could send data somewhere the
project never agreed to. Trying to widen it is an error that says so.

Do **not** configure it in `research/proposals/`. A proposal may be declined and never run, and it
is consumed when accepted — so anything written there is either meaningless or lost. It is also an
agent-written file, and where your data is uploaded is not the agent's decision.

### What gets mirrored

| Hiraku | MLflow |
|---|---|
| goal | experiment (`mlflow_experiment`, else the goal id) |
| run | run, named by the `run_id` |
| provenance columns (`commit`, `seed`, `lockfile_sha256`, `run_kind`, …) | params |
| numeric metrics your entrypoint reported | metrics |
| `run_id`, `goal`, `experiment_id`, `reproducible` | tags `hiraku.*` |
| `mlflow_run_id` column in `runs.csv` | the MLflow run id |

The last row is the point: the two records name each other, so a run found in the MLflow UI can be
traced back to its experiment record and its commit.

### Check it before a long run

```bash
pip install 'mlflow>=2.9'          # or: pip install -e '.[mlflow]' in the tool repo
hiraku-auto tracking               # what is resolved, and whether the server answers
hiraku-auto tracking --experiment dinov3-swap
```

```
backend:     table+mlflow
run table:   .hiraku/runs.csv   (always written)
mlflow uri:  http://mlflow.example.org:5000
experiment:  dinov3-crossdomain
  tag hiraku.experiment_id = dinov3-swap
  tag hiraku.goal = dinov3-crossdomain

mirror reachable
```

**A broken mirror never costs you a run.** If the server is down or `mlflow` is not installed, the
run is recorded in the table as normal, `mlflow_run_id` stays empty, and the launch prints one
warning. Losing a row because a tracking server was unreachable would be a far worse failure than
an un-mirrored run.

## Parallel experiments

```bash
hiraku-auto worktree add vggt-omega
hiraku-auto worktree add vggt-long
```

One checkout per active experiment on one repository — no second clone, no shared object store to
copy. Each has its own working tree and its own environment, so two backends with conflicting
dependencies run at the same time.

A worktree is a full checkout, so it contains its own `.hiraku.yml` and project discovery stops
there. The run table, `research/`, and the leaderboard therefore resolve to the **main** checkout
regardless of which worktree you are in — otherwise each worktree would keep a private table and
nothing would be comparable. Only `experiments/` is local, which is what you want: that is the code.

```bash
cd .worktrees/vggt-omega
hiraku-auto launch vggt-omega:train --commit HEAD --seed 1   # writes to the shared table
```

## Calling the agent

```bash
hiraku-auto goal draft     vo-backend-selection --question "..."   # write the goal record
hiraku-auto agent design   vo-backend-selection      # propose the tree for a goal
hiraku-auto agent analyze  vggt-omega                # write findings from the runs
hiraku-auto agent triage   rig-camera-vo             # classify a failure, decide on a child
hiraku-auto agent discover vo-backend-selection      # literature -> proposals
```

Default command is `claude -p <prompt>`. Precedence, highest first: `--agent-cmd`, then
`HIRAKU_AGENT_CMD`, then `agent.command` in `research/config.md`, then the default. Anything that
reads a prompt argument and prints a reply works — `codex exec`, a local model.

Put the flags a project always needs in its config rather than retyping them:

```yaml
agent:
  command: claude -p --allowedTools WebSearch,WebFetch,Read,Glob,Grep --add-dir ../mapillary_alignment
  unset_env: [ANTHROPIC_API_KEY]
  timeout: 900
```

The resolved command, timeout and dropped variables are recorded in each call's `meta.json`, so a
log says which credential and which tools that call actually had.

### Seeing what it is doing

An agent call is a subprocess that prints nothing until it finishes, and design or discover on a
real tree takes minutes. So every call announces itself first and ticks while it waits:

```
$ hiraku-auto agent design vggt-dinov3
agent:   claude -p   (design -> vggt-dinov3)
context: 5 file(s), 12,904 characters, 2 from outside this project
prompt:  41,203 characters
log:     .hiraku/agent-logs/2026-08-26T110307-design-vggt-dinov3/
waiting for a reply (timeout 900s; Ctrl-C is safe, nothing is written until it returns)
  ... 64s
agent replied in 71.3s
wrote experiments/dinov2-retrieval/experiment.md
```

The tick rewrites one line on a terminal and prints every 30 s when output is piped or redirected,
so a log file from an unattended run stays readable.

Nothing is written to any record until the reply comes back and parses, so Ctrl-C during the wait
leaves the project exactly as it was.

### The log

Every call — including one that fails — writes `prompt.md`, `reply.txt` and `meta.json` under
`.hiraku/agent-logs/<timestamp>-<workflow>-<target>/`. That directory is gitignored: it is a
transcript, not a record.

```bash
hiraku-auto logs                          # the last 20 calls, with durations
hiraku-auto logs --show latest            # the newest reply, in full
hiraku-auto logs --show latest --prompt   # exactly what was sent
hiraku-auto logs --show 2026-08-26T1103   # any call, matched by a prefix of its id
hiraku-auto logs --json                   # machine-readable
```

```
$ hiraku-auto logs
when                 workflow  target       secs   reply   result
-------------------  --------  -----------  -----  ------  ------
2026-08-26T11:03:07  design    vggt-dinov3  71.3   4,102   ok
2026-08-26T10:41:55  discover  vggt-dinov3  184.0  9,540   ok
2026-08-26T10:22:03  draft     vggt-dinov3  38.2   0       error
```

The prompt is kept even when the call fails, so a timeout or a malformed reply leaves you something
to paste into an interactive `claude` session rather than nothing at all.

### Which credential it uses

`claude` prefers `ANTHROPIC_API_KEY` over your claude.ai login when both are available. That has
three consequences, and only the third looks like an error:

- the calls bill to the API account, not your subscription;
- your organisation's connectors do not load;
- if the key is invalid or out of credit, the call fails — and the message you see is the
  *connectors* warning, which does not say that.

```
error: Agent exited 1.
--- stderr ---
⚠ claude.ai connectors are disabled because ANTHROPIC_API_KEY or another auth source is set
  and takes precedence over your claude.ai login · Unset it to load your organization's connectors

ANTHROPIC_API_KEY is set in your environment, so `claude` used it instead of your
claude.ai login — and that key then failed. ...
  env -u ANTHROPIC_API_KEY hiraku-auto ...     # this one command
  hiraku-auto agent ... --unset-env ANTHROPIC_API_KEY
```

Four ways to use the login instead, in the order I would reach for them:

```yaml
# research/config.md front matter — once, for this project
agent:
  unset_env: [ANTHROPIC_API_KEY]
```

```bash
hiraku-auto agent design <goal> --unset-env ANTHROPIC_API_KEY          # one call, repeatable flag
env -u ANTHROPIC_API_KEY hiraku-auto goal draft <id> --question "..."   # one command
grep -rn ANTHROPIC_API_KEY ~/.zshrc ~/.zprofile ~/.bashrc ~/.profile   # find and remove it
```

**None of these touch the key.** A variable is dropped from the copy of the environment handed to
the `claude` subprocess; the parent process, your shell and the key itself are unchanged, so the key
stays available to everything else on the machine. Only revoking it in the Console invalidates it.

`--unset-env` on a call **adds to** `agent.unset_env` rather than replacing it — both are saying
"drop this", so a flag must not silently re-admit what the project decided to drop.

PyCharm run configurations and `.env` files set it too, so check those if the shell looks clean.

When the key is set and the agent command starts with `claude`, the pre-flight line says so before
the call, because it decides who pays:

```
agent:   claude -p   (design -> dinov3-crossdomain)
env:     ANTHROPIC_API_KEY is set — claude will use it INSTEAD of your claude.ai login.
         That bills the API account and disables your org's connectors. Add --unset-env ...
```

A failed call writes `error.txt` next to `prompt.md` in the log directory, so `hiraku-auto logs
--show latest` has the whole thing — both streams — rather than only what was still in scrollback.

### Web access

`claude -p` starts with no tools permitted, and it is non-interactive, so a tool it was not given up
front is requested and denied. `discover` then searches nothing and cites what the model already
believed. That failure is quiet — recalled citations look exactly like read ones — so the tool says
so three times: before the call,

    web:     this command cannot search. `claude -p` is non-interactive, so WebSearch/WebFetch
             are requested and denied, and the citations you get back will be recalled, not read.

in each proposal it writes, at the top of **Where it came from**,

    **[UNVERIFIED: the agent had no web access on this call ...]**

and again at `proposal accept`, which is the last moment before a recalled citation becomes an
experiment. The marker is also written when the reply itself reports the denial, whatever the command
looked like.

How the prompt reaches the agent: `claude` is fed on **stdin** (a variadic `--add-dir <dirs...>`
would otherwise swallow a prompt appended after it, and claude would report that it was given no
input at all); any other command gets the prompt as its last argument. Put `{prompt}` anywhere in
`agent.command` to place it yourself instead.

To let it actually read, name the tools once in `research/config.md`:

```yaml
agent:
  command: claude -p --allowedTools WebSearch,WebFetch,Read,Glob,Grep
```

```bash
hiraku-auto agent discover <goal> --agent-cmd 'claude -p --allowedTools WebSearch,WebFetch'
export HIRAKU_AGENT_CMD='claude -p --allowedTools WebSearch,WebFetch'   # or for one shell
```

### Proposing your own ideas

`discover` is not only for the literature. Put your own directions in a file — one paragraph each,
in your own words — and hand it over:

```bash
hiraku-auto agent discover <goal> --instruction-file notes/my-directions.md
```

Each idea comes back as a proposal held to the same standard as anything the agent found: the bridge
to this project's data, what would refute it, the six investigation conditions, the cost, and the
strongest case against it. The agent searches for what supports or undermines each one — an idea the
literature contradicts becomes a proposal with a real counterargument rather than one quietly
dropped. Those proposals carry `origin: researcher`; the ones it found itself carry
`origin: agent-literature`, so `proposal list` shows which is which.

An idea already covered by an existing node or a declined proposal is reported in `notes` instead of
proposed again — which is why declining beats deleting.

### What the agent may remember

There is no memory between calls: `claude -p` starts a fresh session each time. What looks like
memory comes through the filesystem, and one path mattered — `.hiraku/agent-logs/` holds the prompt
and raw reply of every past call, and the agent has `Read`/`Glob` in the project. A proposal you
deleted could come back, sourced from the reply that first produced it.

Two things prevent that now. The shared rules tell the agent that the state block is the whole
memory and its own transcripts are not evidence, and `init` writes

```json
{ "permissions": { "deny": ["Read(./.hiraku/agent-logs/**)"] } }
```

to `.claude/settings.json`, which Claude Code enforces in `-p` mode too. A project created before
this — or one that already had a settings file, which `init` never overwrites — needs that deny rule
added by hand.

The corollary: a decision that should survive has to be a record. Declining a proposal keeps it, with
its reason, and the next `discover` sees it. Deleting it means the idea can be proposed again, and
should be — nothing in the project says it was considered.

### Project context

`draft`, `design` and `discover` also send excerpts of the project's own files — `CLAUDE.md`, the
README, `docs/**` — so the agent uses your definitions instead of asking for them. Run
`hiraku-auto context` to see exactly what would be sent before the first call; `--context PATH` adds
a file, `--no-context` sends none.

A file over the per-file cap (60,000 characters by default) is cut on a heading boundary and the
excerpt names the sections it left out, so the agent can say what it did not see instead of guessing.
Raise `context.max_bytes_per_file`, pass `--context-bytes N` for one call, or let the agent read the
file itself with `--agent-cmd 'claude -p --allowedTools Read,Glob,Grep --add-dir ../other-repo'`.
Full details in [goal-drafting.md](goal-drafting.md).

### Authentication and cost

No API key is needed if `claude` is already logged in — this package spawns `claude` as a
subprocess and inherits your environment, so whatever authenticates your interactive `claude`
authenticates these calls. Claude Code needs a Pro, Max, Team, Enterprise, or Console account;
`ANTHROPIC_API_KEY` is one option, not a requirement.

**Check where your `-p` usage is billed before running `loop` unattended.** Users have reported
that `claude -p` in print mode bills as API usage rather than against a Max subscription, even when
logged in via OAuth with no `ANTHROPIC_API_KEY` set
([anthropics/claude-code#43333](https://github.com/anthropics/claude-code/issues/43333) — closed,
resolution unclear). Whether that still holds is worth verifying on your own account rather than
assuming either way: run one `hiraku-auto agent analyze` and look at your usage before turning the
loop loose.

Prompts here are not small — the whole goal subtree and its runs go into every call — so the cost
per call scales with the size of the tree. Ways to keep it bounded:

- `--dry-run` prints the prompt and calls nothing. Use it first to see what a call would send.
- `--no-apply` calls the agent but writes nothing.
- `loop --max-iterations N` caps the calls; without `--launch` it starts no runs at all.
- `HIRAKU_AGENT_CMD` points the whole thing at a local model, at which point cost is your GPU.

**The agent decides; the tool writes.** Each workflow sends the project state as JSON plus the rules
for that workflow, and requires one JSON object back. This package applies it. That split is the
point: file writes stay deterministic, a bad suggestion is a diff you can revert, and the agent
cannot wander outside the workflow.

Useful flags:

- `--dry-run` — print the prompt and call nothing. Paste it into any model yourself.
- `--no-apply` — call the agent, write nothing, show what it returned.
- `--show-raw` — print the raw reply alongside.

What each workflow may do:

| Workflow | Writes | Never |
|---|---|---|
| `design` | proposed experiments under a goal | approves or launches anything |
| `analyze` | Findings, Conclusions, verdict; flags a promotion *candidate* | promotes |
| `triage` | the parent's Findings/Conclusions/verdict, the investigation test, any justified child | creates a child for a class A defect, or while the parent is open |
| `discover` | proposals in `research/proposals/` | creates experiments |

Class A (execution defect) is enforced in code: no verdict is set and no child is created, because
the expectation was never tested.

## The loop

```bash
hiraku-auto next vo-backend-selection            # what would happen next; no agent call
hiraku-auto loop vo-backend-selection --max-iterations 5 --launch train
```

The ordering is deterministic Python, not a judgement call: analyse a running node → launch an
approved one → analyse runs with no verdict → triage a concluded one → ask about proposals →
exhausted. The loop stops at every point where the policy says a human decides — a proposal awaiting
approval, an investigation test that failed, a run that exited non-zero. Without `--launch` it will
not start runs at all.

## Running several at once

```bash
hiraku-auto loop <goal> --launch train --max-parallel 8 --gpus 8
```

Every approved node with no runs yet is launched together, up to `--max-parallel`. `--gpus 8` means
devices 0–7; an explicit list works too (`--gpus 0,1,4,5`), and each slot gets one device via
`CUDA_VISIBLE_DEVICES`. More slots than devices wraps, so a slot always has one.

Each run defaults to **its own branch** as the commit ref, not `HEAD`. Parallel siblings usually
hold different code, and the launcher already gives every commit-backed run its own temporary
worktree — so the runs are isolated from each other and stay reproducible, with no durable worktree
to manage. Override with `--commit <ref>` to put them all on one commit.

All rows land in the one project run table. Appending can widen the header, which is a
read-modify-write of the whole file, so the writer takes an exclusive lock for the duration —
without it, two runs finishing at the same moment would interleave and lose rows.

The loop stops on the first failed member and names it. `--max-parallel 1`, the default, is the old
sequential behaviour.

## Deleting a record

There is no undo, so both commands refuse rather than strand anything:

```bash
hiraku-auto goal rm <goal>                      # only if nothing depends on it
hiraku-auto goal rm <goal> --with-experiments    # take its experiment records too
hiraku-auto exp rm <exp>                        # only if it has no children
hiraku-auto exp rm <exp> --with-children        # take the subtree
```

- **A goal with experiments under it is refused.** Every comparison those experiments record is
  only meaningful under this goal's evaluation protocol; removing the goal from under them strands
  them. Move them with `exp set <id> --goal <other>`, or pass `--with-experiments`.
- **An experiment with children is refused**, for the same reason in miniature: they would point at
  a parent that no longer exists. Re-parent with `exp set <id> --parent <other>`, or pass
  `--with-children`.
- **Anything with recorded runs is refused.** The run table is append-only history and is never
  rewritten, so deleting the record leaves rows pointing at nothing and `check` reports them
  forever. `--force` goes through anyway; usually what you want instead is
  `exp set <id> --status abandoned`, which keeps the result and takes the node out of the loop.
- **Git branches and worktrees are never destroyed.** A branch can hold committed work, so `rm`
  prints the exact `git branch -D` and `hiraku-auto worktree remove` commands and leaves the
  decision to you.

Run `hiraku-auto check` afterwards to confirm nothing dangles.

## Promotion

```bash
hiraku-auto promote vggt-omega --run r-20260824-161851-0ed3 --signed-off-by "Qianqian Zou"
```

Five conditions, each checked in code and each printed:

1. **better** — beats the incumbent on the goal's `primary_metric`, and does not degrade
   `gate_metric`.
2. **reproducible** — `run_kind: commit`.
3. **protocol** — the goal record at the run's commit matches the goal record now. This catches
   changing the evaluation protocol to win, which is otherwise invisible.
4. **reproduced** — another seed on the same commit, agreeing within `agreement_tolerance` from
   `research/config.md`.
5. **signed_off** — a name in `guided`; in `auto`, satisfied by (4).

Nothing is written unless all five pass. `--force` records it anyway, marked as forced.

## The report

```bash
hiraku-auto report <goal>                 # to stdout
hiraku-auto report <goal> -o report.md    # to a file
```

Assembles the goal's story from the records: the question, where it stands, the tree, **what is
known**, **what was ruled out**, **what is still open**, the runs, and the gaps.

It is deterministic and calls no model. Every judgement in it is *quoted* from an experiment's
Findings or Conclusions — the prose an agent or you wrote, with the run IDs it cites. This module
only assembles and orders. That is what makes the report safe to regenerate and diff: if it changed,
a record changed.

Two things it will not do: call an unpromoted run a result (it says "a candidate, not a result"),
and hide a thin record — the **Gaps** section lists every node under the goal that fails
`hiraku-auto check`, because a report can only quote what was written.

## Viewing

```bash
hiraku-auto dash -o dashboard.html    # one self-contained page, no network
hiraku-auto ui                        # Streamlit
```

The Streamlit app shows the tree, each node's record and runs, pending proposals and the
leaderboard, and offers the same actions as the CLI — every one of which writes to the markdown
records, so anything done there shows up in `git diff`. Editing prose in the browser is deliberately
absent: findings belong in a record, in a diff a reviewer can read.

## Every command

The complete, generated reference with every flag is [commands.md](commands.md). Summary:

```
init                                   create a project
goal add|draft|list|show|rm            research questions; draft writes one with the agent
exp create|list|show|set|rm            experiments; --parent makes it a child
tree [--goal] [--json]                 the tree
check [--strict]                       validate records and the run table
sync                                   regenerate Children sections
launch <exp>:<entrypoint> [--commit]   run and record
runs [<exp>] [--sort M --ascending]    list or rank
agent design|analyze|triage|discover   call the agent
context [--add PATH] [--json]          project files the agent would be given
tracking [--experiment ID]             where runs are recorded; MLflow reachability
logs [--show latest] [--prompt]        every agent call: prompt, reply, duration
next <goal>                            what the loop would do next
loop <goal> [--launch <entrypoint>]    bounded loop
promote <exp> --run <id>               the promotion gate
proposal list|accept|decline           agent proposals
worktree add|remove|list <exp>         one checkout per active experiment
dash [-o FILE] / ui [--port]           the viewers
```

`check` finds: dangling `parent`/`goal`, duplicate IDs, cycles, a goal that disagrees with the
parent's, a record id that disagrees with its directory, concluded-without-verdict, a verdict on a
node that is not concluded, an unknown `kind`/`status`/`verdict`/`origin`, a missing expectation, a
child whose mode widens its parent's, and a run row naming an unknown experiment.
