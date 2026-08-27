# Hiraku

**Hiraku** is a lightweight workflow and toolkit for AI-assisted data science research.

It defines a shared protocol and conventions around standard data science practices, linking
research goals, implementation attempts, code commits, experiment runs and results. This shared
record enables both guided, human–AI collaboration and bounded autonomous research, with explicit
checkpoints for reviewing and verification. The toolkit provides deterministic support functions via
a command-line interface (`hiraku`) and agent skills (`/hiraku`).

**Why Hiraku?**
In renku (連句), the traditional Japanese art of collaborative linked poetry, hiraku (平句) are the
regular verses where the sequence develops, shifts, and explores new directions — making the name a
natural metaphor for iterative, exploratory research.


## The problem

In the experimental phase of a data science project, the problem, data, and objective are typically
well defined, and the focus shifts to iterative model development (cf. [CRISP-ML](https://ml-ops.org/content/crisp-ml)). Data scientists typically cycle through a sequence of steps:

1. defining a baseline or hypothesis,
2. implementing and debugging,
3. running experiments and tracking metrics,
4. analyzing and comparing results, and
5. iterating or concluding.

While established tools support individual stages of this cycle (Git for version control, Weights &
Biases or MLflow for experiment tracking, and notebooks for analysis, etc), the meta-level
information that connects these stages is often only loosely documented. It is distributed across
artifacts such as personal research notes, code commits, notebooks, experiment runs, checkpoints,
analysis notes, and final method choices, while also incorporating feedback from domain experts and
collaborators.

This fragmentation creates a gap in traceability: ideas, hypotheses, implementation, experiment
lineage, results, and conclusions are not consistently linked or documented as part of a coherent
research process. As AI-assisted and agentic research workflows become more capable, the lack of
conventions becomes an increasingly bottleneck for governance. Without a lightweight but explicit
protocol for capturing the research process, AI systems cannot reliably inspect prior work, understand
why decisions were made, propose meaningful next steps, reproduce conclusions, or effectively support
data scientists through iterative experimentation.

Hiraku addresses this gap by establishing conventions, workflows, and tools for AI-assisted data
science research. It supports two distinct working modes:

* **Guided mode**: A guided, step-by-step AI-assisted research workflow, where the data scientist
  explicitly specifies and approves each step, and inspects and reviews artifacts before proceeding.
* **Auto mode**: An agentic, closed-loop “autoresearch” mode, where the agent implements, runs,
  analyzes, and iterates on experiments within the defined protocol and scope, exposing all artifacts
  for post-hoc human verification.


## Proof of concept

The proof of concept establishes a minimal working version of Hiraku. The proof of concept is complete
when two demo cases can execute this loop in both working modes and expose enough evidence for a human
to review what happened. Metric improvement is measured, but it is not a condition for success. The
initial version includes a first implementation of the command-line interface, the agent skill, an
interactive TUI, and generated reports.

Contributor-facing package boundaries, adapter contracts, and testing guidance are documented in the
[architecture guide](docs/source/architecture.md) and [testing guide](docs/source/testing.md).

## Core workflow: Tracking the experiment life-cycle

An *experiment* is the main implementation unit in the Hiraku framework, representing one concrete
attempt under a research **Goal**. A Goal defines the metric and evaluation protocol; an Experiment
records its hypothesis, parent lineage, branch, source commits, and conclusion; Runs record executions
and evidence. A baseline is represented as an Experiment with `kind: baseline`.

The lifecycle store is SQLite at `.hiraku/state.db`, not an editable Markdown file. This keeps Goal
and Experiment state available while Git branches change. Human-readable Markdown, JSON, CSV, and the
TUI are projections of SQLite. Git still records implementation history, and every Run records the
exact source commit it evaluated.

### Experiment record: SQLite and reports

The authoritative records are stored in these SQLite tables:

```text
goals, experiments, runs, run_parameters, run_metrics, proposals, events
```

Generated reports live under `.hiraku/reports/` and are disposable projections. They summarize the
same information for review; they are not lifecycle input. Experiment branches contain source code,
not copied Goal metadata or launch manifests.

### Workflow

The following sequence illustrates the intended workflow. It can be run step-by-step, guided by the
data scientist, or autonomously iterated end-to-end within approved bounds:

```bash
# Setup
hiraku init
git add .hiraku.yml .hiraku/.gitignore .agents
git commit -m "initialize hiraku"

# 1. define a measurable Goal and import its entrypoints
hiraku goal create heart-disease \
  --metric roc_auc_mean \
  --direction max \
  --launch examples/heart-disease/launch.yml

# 2. create and evaluate the baseline Experiment
hiraku experiment create heart-disease/logistic \
  --kind baseline \
  --hypothesis "Logistic regression establishes the reference model."
hiraku launch heart-disease/logistic:evaluate

# 3. create a hypothesis Experiment; creation checks out its branch
hiraku experiment create heart-disease/random-forest \
  --hypothesis "Random forest improves ROC AUC."
hiraku launch heart-disease/random-forest:evaluate

# 4. compare, inspect lineage, and record a conclusion
hiraku runs heart-disease --sort roc_auc_mean --descending
hiraku experiment tree heart-disease
hiraku experiment conclude heart-disease/random-forest --verdict supports
hiraku report generate heart-disease
hiraku tui
```

Sibling Experiments always start from the Goal baseline, so no manual `git switch` is needed between
them. Children use `--parent <goal>/<experiment>` and start from the parent's frozen evaluated
commit. The first launch freezes an Experiment's exact commit; later source changes require a new
Experiment.

`hiraku run log` or `$HIRAKU_METRICS_PATH` lets project code report evidence. Parameters are immutable
once set; active metrics may be updated. Every validated launch creates a durable Run even when the
child fails or produces no metric.

### Configuration

`hiraku init` creates `.hiraku.yml`, ignored local state, and the repository-local Hiraku skill.

```yaml
version: 1
state:
  backend: sqlite
  path: .hiraku/state.db
reports:
  path: .hiraku/reports
```

Goal entrypoints use fixed argv arrays and are imported into SQLite at Goal creation:

```yaml
entrypoints:
  evaluate:
    command: [uv, run, python, evaluate.py]
    cwd: .
auto:
  allowed_entrypoints: [evaluate]
```

Agent-driven auto mode needs only the approved entrypoint list. A fixed `auto.implement` command is
optional and exists solely for deterministic, non-agent integration loops.


## Command-line interface

### Initialize a project

```bash
hiraku init
```

This creates the default configuration, ignored SQLite state, reports/metrics directories, and the
repository-local agent skill at `.agents/skills/hiraku/`.

### Create and inspect Goals

```bash
hiraku goal create heart-disease --metric roc_auc_mean --direction max --launch examples/heart-disease/launch.yml
hiraku goal list
hiraku goal show heart-disease --json
hiraku goal update heart-disease --objective "Improve validation ROC AUC."
```

When starting from an unfamiliar project, a generic agent adapter can draft a Goal from bounded
repository context. Drafts are review-only until `--apply` is explicitly provided:

```bash
hiraku goal draft heart-disease \
  --question "Maximize ROC AUC for the classifier." \
  --adapter ./agent-adapter
hiraku goal draft heart-disease \
  --question "Maximize ROC AUC for the classifier." \
  --adapter ./agent-adapter \
  --launch examples/heart-disease/launch.yml --apply
```

Goal creation requires a clean committed checkout and captures `HEAD` as the immutable source
baseline. Metric, direction, protocol, constraints, and launch permissions cannot change after Runs
exist.

### Create and inspect Experiments

```bash
hiraku experiment create heart-disease/logistic --kind baseline --hypothesis "Reference model."
hiraku experiment create heart-disease/random-forest --hypothesis "Random forest improves ROC AUC."
hiraku experiment create heart-disease/class-weighted-rf \
  --parent heart-disease/random-forest \
  --hypothesis "Class weighting improves minority-class performance."
hiraku experiment list heart-disease
hiraku experiment show heart-disease/random-forest --json
hiraku experiment tree heart-disease
hiraku experiment conclude heart-disease/random-forest --verdict supports
hiraku experiment abandon heart-disease/failed-attempt
```

Experiment creation creates and checks out `hiraku/<goal>/<experiment>`. Root Experiments branch from
the Goal baseline; children branch from a parent's frozen evaluated commit.

### Launch project code

```bash
hiraku launch heart-disease/random-forest:evaluate
hiraku launch heart-disease/random-forest:evaluate --commit HEAD
```

The launcher validates the Experiment branch, clean checkout, manifest, cwd, and fixed argv. It
creates a Run before execution, injects execution metadata, captures provenance, finalizes the Run,
and propagates the child exit code. `--commit` executes a validated source ref in a temporary
detached worktree; worktrees are not required for normal launches.

Injected environment includes:

```text
HIRAKU_RUN_ID
HIRAKU_GOAL_ID
HIRAKU_EXPERIMENT_ID
HIRAKU_EXPERIMENT_KIND
HIRAKU_GIT_COMMIT
HIRAKU_METRICS_PATH
HIRAKU_PROJECT_ROOT
HIRAKU_STATE_PATH
```

Project code can write metrics with:

```bash
hiraku run log --param seed=42 --metric roc_auc_mean=0.84
```

or by writing `{"parameters": {"seed": 42}, "metrics": {"roc_auc_mean": 0.84}}` to
`$HIRAKU_METRICS_PATH`. The launcher owns the Run row; example programs never edit SQLite.

### Inspect runs and the Leaderboard

```bash
hiraku runs heart-disease
hiraku runs heart-disease --sort roc_auc_mean --ascending
hiraku runs heart-disease --sort roc_auc_mean --descending
hiraku runs heart-disease/random-forest --json
hiraku runs heart-disease --csv heart-disease-runs.csv
```

Without sorting, rows retain creation order. With `--sort`, numeric values are ranked and missing or
non-numeric values remain visible afterward. JSON contains flattened metric and parameter columns.

### Auto mode

When a coding agent is already running in the repository, ask it to use the installed Hiraku skill
and conduct bounded autoresearch for the Goal. The skill is runtime-neutral and drives the shared
core with these machine-readable primitives:

> Use the repository-local Hiraku skill. For Goal `heart-disease`, conduct bounded autoresearch
> using at most 5 Experiments, 20 Runs, and 2 hours. Use only the Goal's approved entrypoints and
> stop before changing its scope, protocol, constraints, manifest, or budgets.

```bash
hiraku auto context heart-disease
hiraku auto baseline heart-disease
hiraku auto proposal create heart-disease/random-forest \
  --hypothesis "Random forest improves ROC AUC."
hiraku auto next heart-disease
# agent edits source on the checked-out branch
hiraku auto implement heart-disease/random-forest --summary "Implemented random forest."
hiraku launch heart-disease/random-forest:evaluate
hiraku auto analyze heart-disease/random-forest
```

For an unattended integration, provide any executable adapter implementing Hiraku's versioned JSON
stdin/stdout protocol:

```bash
hiraku auto loop heart-disease \
  --adapter ./my-agent-adapter \
  --max-experiments 5 \
  --max-runs 20 \
  --time-budget 2h
```

Hiraku does not hard-code an agent vendor, model, SDK, or CLI. The adapter receives `propose`,
`implement`, and `analyze` requests; all lifecycle changes still pass through the same Goal,
Experiment, Run, Git, and event services.

The deterministic manifest-driven fallback remains available for integration examples:

```bash
hiraku auto proposal discover heart-disease
hiraku auto proposal list heart-disease
hiraku auto next heart-disease
hiraku auto loop heart-disease --max-experiments 5 --max-runs 20 --time-budget 2h
```

That fallback runs the approved fixed implementation command from the Goal manifest; it is useful
for deterministic smoke testing but does not generate hypotheses with an AI agent. Both paths stop
before changing Goal scope, protocol, permissions, policy, or resource limits.

### TUI and reports

```bash
uv sync --extra tui
hiraku tui
hiraku report generate heart-disease
```

Reports and the TUI consume the same SQLite query model and show lineage, statuses, verdicts, commits,
metrics, parameters, provenance, and auto proposals. The TUI also exposes guarded lifecycle actions
for eligible Experiments and Runs; it does not bypass the core services.

## Agent skill

`hiraku init` installs one repository-local Agent Skill at `.agents/skills/hiraku/`.

The skill acts as a router for focused workflows that are loaded when required:

- creating and refining an Experiment;
- implementing its current plan;
- launching and analyzing Runs; and
- performing a bounded autonomous research loop.

These are internal skill workflows, not public slash subcommands. Users interact naturally, for
example:

```text
Use the Hiraku skill to create an Experiment for implementing the paper's baseline.

Use the Hiraku skill to implement the next step of random-forest in guided mode.

Use the Hiraku skill to run the approved bounded autonomous loop for heart-disease.
```

The skill calls the CLI and never edits SQLite or generated reports directly.

## Review and approval protocol

The mode guides agent behavior; enforcement remains the responsibility of Git protections,
sandboxing, credentials, and execution infrastructure.

### Guided mode

The data scientist directs the work and reviews the relevant artifacts before the agent proceeds
through these checkpoints:

1. Goal scope and evaluation protocol;
2. Experiment hypothesis and implementation plan;
3. source edits and proposed commit;
4. Experiment launch; and
5. Run analysis and Experiment conclusion.

In guided mode, the agent does not create, edit, commit, launch, conclude, or abandon without explicit
human approval.

### Auto mode

Before auto mode starts, the data scientist approves the Goal, baseline, metric, direction,
evaluation protocol, allowed entrypoints, maximum Experiments, maximum Runs, time/compute budget,
and policy. Within those limits, the agent may create proposals, implement and commit Experiments,
launch Runs, analyze metrics, and conclude hypotheses. A deterministic demo may additionally approve
a fixed manifest implementation command.

It must stop before changing Goal objective, baseline definition, metric, direction, protocol,
constraints, program/policy, permissions, or resource limits. Both modes use the same data model and
core services; they differ only in when the human reviews and approves actions.

## Sources of truth

- SQLite `.hiraku/state.db` records Goal, Experiment, Proposal, Run, provenance, and lifecycle events.
- Git records exact implementation commits and branch history.
- `$HIRAKU_METRICS_PATH` or `hiraku run log` supplies project metrics and parameters.
- Generated Markdown/JSON/CSV reports and the TUI are disposable projections.

Hiraku resolves, validates, and presents the links between these sources; it does not duplicate their
ownership.

## Demo and acceptance criteria

The PoC includes:

1. one small, fast deterministic toy experiment; and
2. one larger deterministic classification experiment; and
3. one generic built-in-dataset classification experiment using scikit-learn Iris.

Both cases exercise guided and bounded-auto modes. See [examples/smoke/README.md](examples/smoke/README.md)
and [examples/heart-disease/README.md](examples/heart-disease/README.md) for every command.
The minimal library-backed case is [examples/iris/README.md](examples/iris/README.md).

## Out of scope for the proof of concept

- Jujutsu or generic VCS plugins;
- MLflow/W&B adapters;
- promotion/current-best pointers or merge automation;
- statistical significance testing;
- durable per-Experiment worktrees;
- scheduler, distributed execution, or remote collaboration;
- dashboard server or artifact browser;
- dataset/environment versioning beyond captured provenance;
- HPO orchestration and distributed sweeps;
- PR/MR integration;
- automatic Goal creation;
- content-addressed artifact storage.

## Roadmap

- richer proposal/literature adapters;
- optional environment and container adapters;
- statistical comparison and explicit promotion policies;
- live dashboard over the existing projection API;
- additional VCS and tracking backends;
- infrastructure-specific launch adapters;
- richer parallel execution once the local lifecycle contract is stable.

## Target installation

For local development:

```bash
git clone https://github.com/SwissDataScienceCenter/hiraku.git
cd hiraku
uv sync --extra dev --extra tui --extra heart-demo
uv run hiraku --help
```

To install `hiraku` from a checkout onto `PATH` without activating its virtual environment:

```bash
uv tool install .
hiraku --help
```


## Appendix: RenkuAI naming framework

**Renku** — the Japanese tradition of collaborative linked verse — is proposed as a naming framework
for a family of research tools. Each name draws first from its role in a Renku sequence, and from
there maps onto a stage or function of research:

1. Hokku (発句) — the opening verse. It establishes the setting, atmosphere, and initial frame of the
   sequence. More broadly, it represents initiation and context-setting: the contribution that creates
   the conditions for everything that follows.
2. Wakiku (脇句) — the second verse, which closely responds to and supports the hokku. It develops the
   opening rather than immediately departing from it. More broadly, it represents reception, grounding,
   and exploration: taking an initial frame and examining what is present within it.
3. Daisan (第三) — literally “the third.” It is the first verse to create a significant shift away from
   the opening pair, linking to what came immediately before while introducing greater distance from the
   original framing. More broadly, it represents critical distance, differentiation, and redirection.
4. Hiraku (平句) — the ordinary or regular verses that make up most of the body of a renku. They are
   where the sequence develops, moves across subjects, changes perspective, and opens new directions.
   More broadly, hiraku represents the generative middle: investigation, iteration, experimentation,
   and sustained development.
5. Ageku (挙句) — the concluding verse. It brings the sequence to a satisfying close without
   necessarily summarizing or exhausting everything that came before. More broadly, it represents
   completion and release: bringing a sequence to an end while leaving space for what may follow.

Together, the proposed naming convention frames research as a sequence of **linked contributions that
establish, explore, challenge, develop, and communicate ideas**.

|Component|Role|Renku principle|
|---|---|---|
|**Hokku**|Knowledge and literature|Establish|
|**Wakiku**|Data exploration|Explore|
|**Daisan**|Review and verification|Challenge|
|**Hiraku**|AI-assisted research tooling|Investigate|
|**Ageku**|Papers, presentations and posters|Communicate|
