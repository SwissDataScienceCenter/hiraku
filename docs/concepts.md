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

A root *record* defines a working scope and goal, and documents the research progress. Child records are focused, reproducible units within that scope that can link code, explicit command executions, and runs. Each record has a producer-defined `type`, such as task, hypothesis, experiment, or dataset. *Runs* record execution traces attributed to one record and, when available, a code commit.

| Term | Data | Summary |
| --- | ---  | --- |
| **Root record** | `hiraku/records/{name}.md` | Defines the scope of a research effort and may provide shared research defaults. |
| **Child record** | `hiraku/records/{name}.md` | A focused investigation that may bind a source commit. A proposed investigation may have no commit. |
| **Command execution** | foreground subprocess | Exit code, streamed output, and tracking attribution. |
| **Run** | backend owned execution trace | A single execution trace, typically a model training or eval run. The tracking backend and logging is user (or agent) managed; Hiraku queries results through a backend adapter.|

Records are stored as Markdown with YAML front matter. `Record.id` is the OKF ID: its bundle-relative path without `.md`. It is derived from placement and never persisted in front matter. `Vault` owns the bundle root, loading, saving, and discovery. `parent_id` and `related_to` declare relationships to concept IDs, independently of directory structure. Moving a document changes its ID.

Record Markdown bodies are trimmed of leading and trailing whitespace when read
or saved. Whitespace within the body is preserved.

`status` describes the OKF concept lifecycle: `draft`, `stable` (the default),
or `deprecated`. The optional `state` field describes research progress using
workflow-defined values such as `drafted`, `launched`, or `concluded`.
Workflow session statuses are separate from these record fields.

All record fields are local. Consumers needing ancestor information must traverse the declared graph themselves. CLI and MCP run queries explicitly walk parent links when selecting metric directions, stopping at an explicit metrics list. `hiraku record show PATH` displays only that document's fields and body.

Saving validates one record and its destination without scanning the vault. Replacing a file requires `overwrite=True`. Loading, saving, updating, and deleting do not enforce collection integrity. `Vault.validate()` explicitly checks missing relationship targets; cycles are permitted.

At any time, a single record can be set as "active". By default, creation activates the new record. Switch with `hiraku record set <record-path>`. Hiraku stores its bundle-relative path as `active_record_id` in `hiraku.yml`. Inspect it with `hiraku status`.

!!! note
    Hiraku is agnostic to git state of metadata as far as possible. The recommended workflow is to commit the metadata records in `hiraku/*`. When switching branches, Hiraku simply lists what is currently visible in the working directory. For advanced workflows using multiple branches, merging and conflict resolution is a user (or agent) responsibility.


## Tracking Backend

A **tracking backend** is an interface that provides a uniform API for querying and listing runs. Hiraku ships with CSV, Weights and Biases, and MLflow adapters. Project configuration selects the backend. The backend needs to:

- List runs from the project tracking source, optionally filtered by record ID.
- Return normalized runs with a required backend run ID, metrics, and parameters; record ID, commit, timestamp, and URL attribution may be absent.

Set `tracking_backend` in `hiraku.yml` to select the tracking source for every
record in the project. Records cannot override the project setting. Backend
configuration can use a shorthand name or a mapping with `type` identifying the
adapter:

```yaml
tracking_backend: csv
```

`hiraku runs` lists runs attributed to the active record, or to the record given with `--record ID`. Add `--all` to list every attributed run from the project tracking source. In Python, create an adapter with `TrackingBackendFactory(config).create(config.tracking_backend)` and call `list_runs(record_id=record.id)` for one record or `list_runs()` for the whole source. Training code calculates and logs results directly; Hiraku does not own metric logging. A launch that never logs a row will not appear as a run.

## Command execution

Commands are not record metadata. Pass the complete argv directly to
`hiraku run`; a dispatcher can prepare repeatable project-specific runtime
behavior around that invocation. Command execution runs synchronously in the
project working tree; tracked runs own measurements and can be inspected after
the command exits. See the [CLI reference](api/cli.md) for command options.

## Agent Skill

`hiraku init` installs copies of the project-local skill under
`.agents/skills/hiraku/`, `.claude/skills/hiraku/`, `.pi/skills/hiraku/`, and
`.codex/skills/hiraku/`. Invoke it as `$hiraku` in Codex or `/hiraku` in Claude Code.

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
| **Record** | `hiraku/records/{name}.md` | Research content of any kind, including tasks, experiments, literature, datasets, and context. |
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
hiraku record create heart-disease-prediction \
  --title 'Heart disease prediction' --type task \
  --set 'goal=Develop models for heart disease prediction'

# alternatively, create it with the agent skill inside a coding harness
# /hiraku "create a new root record for heart-disease prediction"

# query the record and Hiraku status
hiraku record show heart-disease-prediction --json
hiraku status

# 3. create a child proposal and implement it
hiraku record create heart-disease-prediction/logistic-regression \
  --title 'Logistic regression' --parent heart-disease-prediction --type experiment \
  --set 'summary=Logistic regression baseline' \
  --set 'hypothesis=Regularized logistic regression provides a calibrated baseline'
# alternatively, use the agent skill to implement the proposal
# /hiraku "implement the logistic baseline record"
# implement and commit the code change, then bind the resulting commit
git commit -m "implement logistic baseline"
hiraku record update heart-disease-prediction/logistic-regression --commit HEAD

# 4. run an arbitrary command and list attributed runs
hiraku run --record heart-disease-prediction/logistic-regression -- uv run python train.py
hiraku runs --record heart-disease-prediction/logistic-regression --sort val/log_loss --ascending

# 5. review the runs and document the conclusion on the child record
hiraku record update heart-disease-prediction/logistic-regression --set decision=keep
hiraku record edit heart-disease-prediction/logistic-regression

# 6. summarize reviewed findings in the root record
hiraku record edit heart-disease-prediction
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
