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

hiraku-auto goal add vo-backend-selection \
  --metric ate_rmse_m --direction minimize --target 3.0 \
  --gate-metric completion_rate \
  --question "Which VO backend produces usable trajectories on NATIX video?"

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
hiraku-auto agent design   vo-backend-selection      # propose the tree for a goal
hiraku-auto agent analyze  vggt-omega                # write findings from the runs
hiraku-auto agent triage   rig-camera-vo             # classify a failure, decide on a child
hiraku-auto agent discover vo-backend-selection      # literature -> proposals
```

Default command is `claude -p <prompt>`. Override with `--agent-cmd` or `HIRAKU_AGENT_CMD`
(`codex exec`, a local model, anything that reads a prompt argument and prints a reply).

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
goal add|list|show                     research questions
exp create|list|show|set               experiments; --parent makes it a child
tree [--goal] [--json]                 the tree
check [--strict]                       validate records and the run table
sync                                   regenerate Children sections
launch <exp>:<entrypoint> [--commit]   run and record
runs [<exp>] [--sort M --ascending]    list or rank
agent design|analyze|triage|discover   call the agent
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
