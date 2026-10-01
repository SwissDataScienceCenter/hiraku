# Hiraku concepts


## Experiment lifecycle

In the model development phase of a data science project, the data and objective are (relatively) well-defined, and the focus shifts to iterative model development (cf. [CRISP-ML](https://ml-ops.org/content/crisp-ml)). Data scientists typically cycle through a sequence of steps:

1. defining an experiment (e.g., a baseline, hypothesis or diagnostic),
2. implementation and debugging,
3. executing runs and tracking metrics,
4. analyzing and comparing results, and
5. iterating or concluding; revisit the research goal if needed.

While standard tools support individual stages of this cycle (e.g., Git for version control, Weights & Biases or MLflow for experiment tracking, and notebooks for analysis, etc), the meta-level information that connects these stages is often only loosely documented.

This fragmentation creates an inherent barrier for effective Human-AI collaboration: proposals, hypotheses, implementation, experiments, diagnostics, results, and conclusions are not consistently linked or documented as part of a coherent research process. Without a lightweight but explicit protocol for capturing the research process, AI systems cannot reliably inspect prior work, understand why decisions were made, propose meaningful next steps, to effectively support data scientists through iterative experimentation. Similarly, without a metadata trace, the data scientist cannot track, inspect and verify the output of an autonomous research agent.

## Records and runs

Hiraku uses two lightweight data structures to track the model-development lifecycle: records and runs.

Only records are managed directly by Hiraku. Runs are owned by a user-managed tracking backend (e.g., Weights & Biases, MLflow, CSV, etc), and are queried via a backend interface.

A root *record* defines a working scope and goal, and documents the research progress. Child records are focused, reproducible units within that scope that can link code, execution entrypoints, and runs. Records carry no required semantic type; users may create them for baselines, hypotheses, diagnostics, model runs, ablations, hyperparameter optimization, etc. *Runs* record execution traces attributed to one record and, when available, a code commit.

| Term | Data | Summary |
| --- | ---  | --- |
| **Root record** | `hiraku/records/{root_id}/{root_id}.md` | Defines the scope of a research effort and may provide shared fields and entrypoints. |
| **Child record** | `hiraku/records/{root_id}/{record_id}.md` | A focused investigation that may bind a source commit and entrypoints. A draft investigation may have no commit. |
| **Run** | backend owned execution trace | A single execution trace, typically a model training or eval run. The tracking backend and logging is user (or agent) managed; Hiraku only queries results via a compatibility backend.|

Records are stored as Markdown with YAML front matter. This allows users and agents to directly read and modify them. `parent_id` defines the logical tree: each record has at most one parent, and all descendants are stored flat in their root's folder. IDs are unique across the project.

Fields inherit through `parent_id` by default: the nearest definition wins, an explicit `null` clears an inherited field, and entrypoints merge by name. `id`, `parent_id`, `related_to`, `type`, `commit`, `status`, and `decision` stay local. `related_to` is a list of cross references; it does not affect inheritance. `hiraku record show ID` displays effective fields and their sources, while `--raw` shows stored fields.

At any time, a single record can be set as "active". By default, creation activates the new record. The user can switch explicitly with `hiraku record set <record-id>`. The current status can be queried with `hiraku status`.

!!! note
    Hiraku is agnostic to git state of metadata as far as possible. The recommended workflow is to commit the metadata records in `hiraku/*`. When switching branches, Hiraku simply lists what is currently visible in the working directory. For advanced workflows using multiple branches, merging and conflict resolution is a user (or agent) responsibility.


### CLI

The CLI provides a standardized interface for managing data records, .e.g

```bash
hiraku init  # initialize at the current Git repository root
hiraku init PATH --ignore-git  # explicitly skip Git-root validation
hiraku init --template ./path/to/template  # apply a local project overlay
hiraku init --example <name>  # apply the src/ tree of a packaged example

hiraku record create <root-id>
hiraku record create <child-id> --parent <root-id> --type experiment
hiraku record list
hiraku record tree
hiraku record show <record-id>  # effective fields and their sources
hiraku record show <record-id> --raw  # stored fields
hiraku record update <record-id> --commit HEAD
hiraku record delete <record-id>

hiraku status  # show the active record

hiraku runs --record <record-id>
hiraku runs --record <root-id> --params seed,lr --metrics train/accuracy,val/accuracy --sort val/accuracy --descending
```

### Python API

Hiraku data records are managed by the [Python API](api/python/index.md). All other user interfaces (CLI, dashboard, agent harness, ...) should refer to the Python API wherever possible.

```python
from hiraku.base import BaseRecord
from hiraku.record import Entrypoint, Metric, Record, list_records, load_record
from hiraku.run import Run
```


## Tracking Backend

A **tracking backend** is an interface that provides a uniform API for querying and listing runs. Hiraku ships with CSV, Weights and Biases, and MLflow adapters; additional adapters can be registered by users. The backend needs to:

- List runs from the tracking source selected by a record.
- Return normalized runs with a required run ID, metrics, and parameters; record ID, commit, timestamp, entrypoint, and URL attribution may be absent.

CSV is the default project backend. A record may set `tracking_backend` to
override the project default. Backend configuration uses one common mapping
shape, with `type` identifying the adapter:

```yaml
tracking_backend:
  type: csv
```

`hiraku runs --record ID` queries the selected record's tracking source and lists its attributed runs. Training code calculates and logs results directly; Hiraku does not own metric logging. A launch that never logs a row will not appear as a run.

## Entrypoints

Records can define named **entrypoints** for launching runs. Entrypoints document reproducible code invocations, including default hyperparameters or command-line arguments, and provide convenient functions for users and agents to run or dispatch code.

Entrypoints are defined in record YAML front matter with the `entrypoints` key, e.g.,

```yaml
entrypoints:
  train:
    command: ["uv", "run", "python", "train.py"]
    args:
      model: logistic
      seed: 42
```

Entrypoints can be invoked via the CLI:

```bash
hiraku launch <entrypoint>  # uses the active record
hiraku launch <entrypoint> --record <record-id>  # explicit attribution
hiraku launch <entrypoint> --record <record-id> -- --seed 1  # override defaults
```

The project-owned dispatcher controls Git behavior:
```bash
hiraku launch <entrypoint> --record <record-id>                      # current working state
hiraku launch <entrypoint> --record <record-id> --dispatch worktree  # exact commit
```

Hiraku injects `HIRAKU_RECORD_ID`, `HIRAKU_RECORD_FILE`, `HIRAKU_ROOT`,
and `HIRAKU_ENTRYPOINT`. The
dispatcher reports `HIRAKU_COMMIT` and `HIRAKU_GIT_STATE`: `launch.sh` reports
the current HEAD and whether the tree is clean or dirty, while `worktree.sh`
reports the exact checked-out commit and a clean state.

The user can define custom dispatch scripts, e.g.

```bash
hiraku launch <entrypoint> --record <record-id> --dispatch runai
```

This invokes the project-owned `hiraku/dispatch/runai.sh`, which inherits the
launch context in its environment and receives the full command after `--`.
The default `launch.sh` runs the current state, while `worktree.sh` creates an
isolated checkout. Child stdout and stderr stream directly to the terminal.
Dispatcher names may omit `.sh`, so `--dispatch worktree` selects `worktree.sh`.

## Agent Skill

`hiraku init` installs the project-local skill for Codex at
`.agents/skills/hiraku/` and for Claude Code at `.claude/skills/hiraku/`.
Invoke it as `$hiraku` in Codex or `/hiraku` in Claude Code. Other coding
harnesses can use the same `SKILL.md` when they support compatible project-local
skills.

## Actions, Programs, Context

!!! info
    Agent execution supports `create task`, `create experiment`, and `implement`.
    Programs, scheduling, and automatic loops remain future work.

Hiraku manages agent integration with actions, programs, and context. *Actions* define one-step execution patterns that combine deterministic hooks with LLM instructions and have a validated start and end state. A *program* is intended to define a longer research workflow. Actions execute through `hiraku agent`; programs are metadata only for now. Bundled actions are resolved at runtime, while a project action of the same ID takes precedence. `hiraku init` does not copy bundled actions into the project. *Context* records hold project-relevant information for future agent workflows.


| Term | Data | Summary |
| --- | ---  | --- |
| **Action** | `hiraku/actions/{action_id}/{action.md,hooks.py}` | A project-defined action that overrides a bundled action of the same name. |
| **Program** | `hiraku/programs/{program_id}/{program.md,program.py}` | A named research workflow that defines agent instructions, permissions, resource limits, checkpoints, and stopping conditions. |
| **Context** | `hiraku/contexts/{context_id}/context.md` | Curated context files, e.g. literature, cross-task summaries, etc. that can be referenced. |

Programs, action and context records are managed via CLI, e.g.

```bash
hiraku program create <program-id>
hiraku program list
hiraku context create <context-id>
hiraku context list
hiraku action create <action-id> --set 'accepted_types=[task, experiment]'
hiraku action list
```

Typed records have an explicit local status. Type definitions live at
`hiraku/types/<type>.md` and fall back to bundled definitions. Like actions, a
`RecordType` derives from `BaseRecord`, with YAML front matter and a Markdown
description. Existing project `<type>.yml` definitions are also readable;
Markdown takes precedence within the same directory.

Types declare `initial_status` and a `lifecycle` list of labels, without a
transition graph. The initial status is valid even if it is not repeated in
the lifecycle. List order documents the lifecycle; it does not restrict moves.
Actions declare their own source and destination and validate both against the
type's labels. Manual status edits remain flexible. New bundled typed records
start at `draft`; older statusless records remain readable.

Inspect resolved definitions with `hiraku type list` and
`hiraku type show experiment` (both support `--json`). Project definitions
override bundled types with the same ID.

For example, a project can override the experiment lifecycle with
`hiraku/types/experiment.md`:

```yaml
---
id: experiment
initial_status: draft
lifecycle: [draft, implemented, running, ready, completed]
---
A research experiment with a hypothesis.
```

Type definitions specify lifecycle structure. Field requirements and scientific
validation are deferred.

```bash
hiraku agent create experiment --record my-task -m "Test a deeper backbone"
hiraku agent create experiment --record baseline -m "Extend this experiment"
hiraku agent create task -m "Investigate calibration drift in the validation cohort"
hiraku agent implement --record deeper-backbone
```

For experiment actions, `--record` defaults to the active record. For `create`, it selects the parent,
which may be a task or experiment. For `implement`, it selects the experiment.
`implement` requires a clean starting tree. Hiraku commits the new work, binds
its hash, and advances to `implemented`. Both actions accept
`--provider codex|claude` and `--model MODEL`;
Codex also accepts `--reasoning-effort`. Each attempt writes its prompt, reply
or error, and metadata under `hiraku/agent-logs/`. Action Markdown contains the
output format and workflow instructions; `-m` adds only the user's research
brief. Both experiment actions work without `-m`, using the selected record as
context. Task creation requires `-m` and creates and activates a root task.
Creation runs Codex in a read-only sandbox, or Claude without tools.
Hiraku creates the record and assigns its parent, type, and status itself.

Provider commands and file changes stream to stderr, alongside deterministic
checks, record creation, commits, commit binding, and status changes. `--json`
keeps stdout reserved for the final JSON result. Each attempt also stores
`provider.log` (the provider stream) and `events.jsonl` (timestamped activity).

Action Markdown declares `effect: create|transition` and relevant statuses.
An action may also declare `hooks: hooks.py`.
Only a declared local hook module executes. Hooks receive an `ActionContext`
containing the selected record, related records, its attributed runs, and
project config. Hooks can call `context.report(kind, message, **details)` to
report deterministic changes to the terminal and event log, and use
`context.state` to carry values between phases.
The supported synchronous phases are `precondition`, `before`,
`after`, `postcondition`, and `cleanup`; conditions return `ConditionResult`.
In-process hooks are trusted project code. The current runtime includes Codex
and Claude CLI adapters; further providers and actions can use the same interface.



## Example workflows

The following sequence illustrates the intended workflow.

### Interactive workflow

```bash
# Setup and status
hiraku init
hiraku status

# 1. optionally, initialize context (not implement yet)
hiraku context create literature
hiraku-agent context update literature "Create a curated literature review for heart-disease prediction"

# add literature to the active context
hiraku context set literature
# print files that would be added to the context
hiraku context

# 2. create the research root; creation makes it active
hiraku record create heart-disease --type task \
  --set 'goal=Develop models for heart disease prediction'

# alternatively, create it with the agent skill inside a coding harness
# /hiraku "create a new root record for heart-disease prediction"

# query the record and Hiraku status
hiraku record show heart-disease --json
hiraku status

# 3. create a child proposal and implement it
hiraku record create logistic-baseline --parent heart-disease \
  --type experiment \
  --set 'summary=Logistic regression baseline' \
  --set 'hypothesis=Regularized logistic regression provides a calibrated baseline'
# alternatively, use the agent skill to implement the proposal
# /hiraku "implement the logistic baseline record"
# implement and commit the code change, then bind the resulting commit
git commit -m "implement logistic baseline"
hiraku record update logistic-baseline --commit HEAD \
  --set 'entrypoints={train: {command: [uv, run, python, train.py]}}'

# 4. launch the entrypoint and list attributed runs
hiraku launch train --record logistic-baseline
hiraku runs --record logistic-baseline --sort val/log_loss --ascending

# 5. review the runs and document the conclusion on the child record
hiraku record update logistic-baseline --set decision=keep
hiraku record edit logistic-baseline

# 6. summarize reviewed findings in the root record
hiraku record edit heart-disease
# or, with the skill: /hiraku "summarize the reviewed child records"
```

The same workflow can be carried out through the installed Hiraku skill in a
coding agent. The [Heart Disease example](examples/heart-disease/README.md)
shows prompts for both a complete investigation and individual steps.

### Autoresearch workflow

The packaged program describes an autonomous loop. Read its instructions and
the research history before starting it in a coding agent; agree on the
objective, branch, budget, and stopping condition with the user.

```bash
hiraku program show autoresearch
hiraku program show interactive
hiraku record tree
hiraku runs --record heart-disease --json
```

The [Autoresearch example](examples/autoresearch/README.md) shows how to use
the program with the project-local skill and a bounded dispatcher. The skill
can be invoked with `/hiraku "run the autoresearch program for heart-disease"`.


Actions declare `accepted_types` and an optional `default_type`. Creation
resolves the positional type first, then the default; bundled `create` requires
an explicit type. Other actions infer the type from the selected or active
record. An explicit type must match that record. Optional `--id` assigns a
creation ID deterministically, with validation before the provider runs.

The common create prompt renders `{{type_prompt}}`, `{{state}}`, and
`{{instruction}}`. Its `type_prompts` mapping selects a local Markdown
fragment whose front matter declares parent types, `requires_prompt`, and
activation. Hiraku sets the type-defined initial status. One action and shared
hooks handle both task and experiment creation.
