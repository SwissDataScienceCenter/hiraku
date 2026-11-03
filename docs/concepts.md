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

Hiraku uses records, foreground command execution, and tracked runs to document the model-development lifecycle.

Records and command execution are managed directly by Hiraku. Runs are owned by a user-managed tracking backend (e.g., Weights & Biases, MLflow, CSV, etc), and are queried via a backend interface.

A root *record* defines a working scope and goal, and documents the research progress. Child records are focused, reproducible units within that scope that can link code, explicit command executions, and runs. Records carry no required semantic type; users may create them for baselines, hypotheses, diagnostics, model runs, ablations, hyperparameter optimization, etc. *Runs* record execution traces attributed to one record and, when available, a code commit.

| Term | Data | Summary |
| --- | ---  | --- |
| **Root record** | `hiraku/records/{root_id}/{root_id}.md` | Defines the scope of a research effort and may provide shared research defaults. |
| **Child record** | `hiraku/records/{root_id}/{record_id}.md` | A focused investigation that may bind a source commit. A proposed investigation may have no commit. |
| **Command execution** | foreground subprocess | Exit code, streamed output, and tracking attribution. |
| **Run** | backend owned execution trace | A single execution trace, typically a model training or eval run. The tracking backend and logging is user (or agent) managed; Hiraku only queries results via a compatibility backend.|

Records are stored as Markdown with YAML front matter. This allows users and agents to directly read and modify them. `parent_id` defines the logical tree: each record has at most one parent, and all descendants are stored flat in their root's folder. IDs are unique across the project.

Fields inherit through `parent_id` by default: the nearest definition wins and an explicit `null` clears an inherited field. When a child is created, its effective `metrics` are copied into its YAML front matter unless explicitly set on the child. These values are snapshots: later parent edits do not rewrite existing children. `id`, `parent_id`, `related_to`, `type`, `commit`, `status`, and `decision` stay local. `related_to` is a list of cross references; it does not affect inheritance. `hiraku record show ID` displays effective fields and their sources, while `--raw` shows stored fields.

At any time, a single record can be set as "active". By default, creation activates the new record. The user can switch explicitly with `hiraku record set <record-id>`. The current status can be queried with `hiraku status`; its `tracking_backend` field shows the project's backend.

!!! note
    Hiraku is agnostic to git state of metadata as far as possible. The recommended workflow is to commit the metadata records in `hiraku/*`. When switching branches, Hiraku simply lists what is currently visible in the working directory. For advanced workflows using multiple branches, merging and conflict resolution is a user (or agent) responsibility.


## Tracking Backend

A **tracking backend** is an interface that provides a uniform API for querying and listing runs. Hiraku ships with CSV, Weights and Biases, and MLflow adapters. Record configuration and the CLI select these built-in backends. The backend needs to:

- List runs from the tracking source selected by a record.
- Return normalized runs with a required backend run ID, metrics, and parameters; job ID, record ID, commit, timestamp, and URL attribution may be absent. Backend-specific fields remain in `raw`.

Set `tracking_backend` in `hiraku.yml` to select the tracking source for every
record in the project. Records cannot override the project setting. Backend
configuration can use a shorthand name or a mapping with `type` identifying the
adapter:

```yaml
tracking_backend: csv
```

`hiraku runs` lists runs attributed to the active record, or to the record given with `--record ID`. Add `--all` to list every attributed run from the project tracking source. In Python, `record.runs(config=config)` returns the record's own runs, and `record.resolve_tracking_backend(config=config).list_runs()` lists the whole source. Training code calculates and logs results directly; Hiraku does not own metric logging. A launch that never logs a row will not appear as a run.

## Command execution

Commands are not record metadata. Pass the complete argv directly to
`hiraku run`; a dispatcher can prepare repeatable project-specific runtime
behavior around that invocation. Command execution runs synchronously in the
project working tree; tracked runs own measurements and can be inspected after
the command exits. See
[Command execution](guides/execution.md) for details.

## Agent Skill

`hiraku init` installs the canonical project-local skill at
`.agents/skills/hiraku/`. Codex, Pi, and Gemini CLI discover skills from this
shared directory. For Claude Code, initialization creates
`.claude/skills/hiraku/` as a symlink to the canonical skill (or a copy on
systems that cannot create directory symlinks). Invoke it as `$hiraku` in Codex
or `/hiraku` in Claude Code.

## Workflows and context

A *workflow* is a project-defined LangGraph procedure. Its graph controls
execution and validates responses; its Markdown can hold editable instructions
and defaults. The agent starts and resumes a workflow through the Hiraku MCP
server, which `hiraku init` configures for Claude Code and Codex. Supporting
material is stored as records with `type: context` and can be linked to other
records.

| Term | Data | Summary |
| --- | --- | --- |
| **Workflow** | `hiraku/workflows/{workflow_id}/{workflow.py,workflow.md}` | A LangGraph graph with optional editable instructions and input defaults. |
| **Record** | `hiraku/records/{root_id}/{record_id}.md` | Research content of any kind, including tasks, experiments, literature, datasets, and context. |
| **Workflow session** | `.hiraku/langgraph-checkpoints.sqlite` | One graph execution, identified by a workflow session ID that is also its LangGraph thread ID. |

Hiraku ships the `collaborative` workflow as an editable copy in your
project. See the [Workflows guide](guides/workflows.md) for how to write and
run a graph.

```bash
hiraku workflow list
hiraku record create literature --type context
hiraku record list
```

## Example workflows

The following sequence illustrates the intended workflow.

### Interactive workflow

```bash
# Setup and status
hiraku init
hiraku status

# 1. optionally, create a context record
hiraku record create literature --type context
# Ask your coding agent to create a curated literature review.

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
hiraku record update logistic-baseline --commit HEAD

# 4. run an arbitrary command and list attributed runs
hiraku run --record logistic-baseline -- uv run python train.py
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

LangGraph workflows can guide interactive research or run a bounded procedure.
Inspect the workflow definition and research history before starting one.

```bash
hiraku workflow list
hiraku workflow start collaborative --arguments-file arguments.json
hiraku record tree
hiraku workflow status <workflow-session-id>
hiraku runs --record heart-disease --all --json
```

The [Autoresearch example](examples/autoresearch/README.md) shows how to use
the LangGraph workflow with the project-local skill and a bounded dispatcher.
