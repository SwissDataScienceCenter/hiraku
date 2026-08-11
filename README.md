# Hiraku

**Hiraku** is a lightweight workflow and toolkit for AI-assisted data science research.

It defines a shared protocol and conventions around standard data science practices, linking experiment scope and documentation, code commits, experiment runs and results. This shared record enables both guided, human–AI collaboration and bounded autonomous research, with explicit checkpoints for reviewing and verification. The toolkit provides deterministic support functions via a command-line interface (`hiraku`) and agent skills (`/hiraku`).

**Why Hiraku?**
In renku (連句), the traditional Japanese art of collaborative linked poetry, hiraku (平句) are the regular verses where the sequence develops, shifts, and explores new directions — making the name a natural metaphor for iterative, exploratory research.

> Treat the name as internal code name; better suggestions are welcome :)


## The problem

In the experimental phase of a data science project, the problem, data, and objective are typically well defined, and the focus shifts to iterative model development (cf. [CRISP-ML](https://ml-ops.org/content/crisp-ml)). Data scientists typically cycle through a sequence of steps:

1. defining a baseline or hypothesis,
2. implementing and debugging,
3. running experiments and tracking metrics,
4. analyzing and comparing results, and
5. iterating or concluding.

While standard tools support individual stages of this cycle (Git for version control, Weights & Biases or MLflow for experiment tracking, and notebooks for analysis, etc), the meta-level information that connects these stages is often only loosely documented.

This fragmentation creates inherent barrier for effective human-AI collaboration: ideas, hypotheses, implementation, experiment lineage, results, and conclusions are not consistently linked or documented as part of a coherent research process. Without a lightweight but explicit protocol for capturing the research process, AI systems cannot reliably inspect prior work, understand why decisions were made, propose meaningful next steps, reproduce conclusions, or effectively support data scientists through iterative experimentation.

Hiraku addresses this gap by establishing conventions, workflows, and tools for AI-assisted data science research. It supports two distinct working modes:

* **Guided mode**: A guided, step-by-step AI-assisted research workflow, where the data scientist explicitly specifies and approves each step, and inspects and reviews artifacts before proceeding.
* **Auto mode**: An agentic, closed-loop “autoresearch” mode, where the agent implements, runs, analyzes, and iterates on experiments within the defined protocol and scope, exposing all artifacts for post-hoc human verification.


## Proof of concept

The proof of concept establishes a minimal working version of Hiraku. The proof of concept is complete when two demo cases can execute this loop in both working modes and expose enough evidence for a human to review what happened. Metric improvement is measured, but it is not a condition for success. The initial version will include a first implementation of the command-line interface and the agent skill.

## Core workflow: Tracking the experiment life-cycle

An *experiment* is the main unit in the Hiraku framework, representing a user-defined implementation scope. It is roughly comparable to the work captured by a Git feature branch, for example, an experiment may introduce a baseline, reproduce an existing method, evaluate a new hypothesis, or capture a similarly scoped, user-defined research task. All information relevant to the experiment is recorded in a markdown file, linking description, implenentation plan, commits and experimental runs. We fall back to standard tooling whereever possible:
- Git is used for code and documentation tracking, branches capture experiment related commits.
- GitHub pull requests (or GitLab merge requests) provide a standard reviewing surface before an experiment is concluded and merged into main.
- A tracking backend (MLflow, CSV/TSV table, W&B, etc) is used to track experiment runs and metrics.

### Experiment record: `experiment.md`

The only data that is directly managed are experiment records. Each experiment is recorded in a single markdown file:
```text
experiments/<experiment-id>/experiment.md
```
The `experiment.md` file links the following elements:
- **Documention** describing the experiment scope, implementation, and results,
- **Git commits** via a specified experiment branch,
- **Entrypoints** specified in a `launch.yml`
- **Runs** for tracking metrics, via a specified backend (MLflow, CSV/TSV table, W&B, etc).

The user and agent continuously update the markdown to document ideas, code, changes and results. Hiraku cli (`hiraku`) and agent skill (`/hiraku`) supports with experiment creation, implementation, documentation, and run comparison (leaderboard).


**Example:**
```markdown
---
id: logreg-baseline
branch: exp/logreg-baseline
mode: guided
mlflow_experiment_id: "42"
---

# Logistic regression baseline

## Scope: Hypothesis or task

Implement and evaluate a reproducible logistic regression baseline.

## Implementation plan

## Evaluation protocol

## Findings

## Conclusions

## Decisions and next steps
```

### Workflow
The following sequence illustrates the intended workflow. The sequence can be run step-by-step, guided by the data scientist, or autonomously iterated end-to-end, in which case the data scientist reviews the final experiment.md, code and run data, optionally exposed via a corresponding pull request.

```bash
# Setup
hiraku init  # creates hiraku project and configuration file `.hiraku`

# 1. create experiment
hiraku exp create logreg-baseline --mode guided --tracking-backend mlflow  # deterministic via cli
git checkout exp/logreg-baseline  # default branch for experiment code
/hiraku "create an experiment that implements a logistic regression baseline"  # agent assisted workflow
hiraku exp list  # list all experiments
hiraku exp show logreg-baseline  # experiment details, --json returns machine-readable output

# 2. implement and commit (user guided)
/hiraku "implement logreg-baseline"
git commit  # data scientist reviews code and commits

# 3. launch experiment and track on mlflow 
hiraku launch logreg-baseline:train  # training entrypoint 
hiraku launch logreg-baseline:hpo  # hyperparameter optimization entrypoint
hiraku runs logreg-baseline  --sort val/cross_entropy # list all mlflow runs that link to the experiment. each run refers to experiment-id, commit, args or parameters, which can be listed here

# 4. analyzing and updating documentation
/hiraku "compare the new runs and update the experiment.md, then create a pull request for reviewing"

# 5. data scientist inspects, code, results etc and decides next steps
hiraku runs logreg-baseline  # experiment leaderboard
hiraku runs  # cross-experiment leaderboard
```


### Configuration

`hiraku init` creates `.hiraku.yml`, the experiment directory, and the repository-local Hiraku skill.

An MLflow-backed project uses configuration shaped like:

```yaml
experiments_dir: experiments
experiment_template: experiments/template.md
tracking:
  backend: mlflow
  tracking_uri: http://localhost:5000
```


## Command-line interface

> This section defines the intended CLI shape for the proof of concept.

Short command names are primary. `hiraku experiment` is also accepted as a long alias for `hiraku exp`.

### Initialize a project

```bash
hiraku init
```

This creates the default configuration, `experiments/`, and install agent skills `.agents/skills/hiraku/`.

### Create and inspect experiments

```bash
hiraku exp create logreg-baseline
hiraku exp create larger-context-window --mode auto
hiraku exp list
hiraku exp list --json
hiraku exp show logreg-baseline
hiraku exp show logreg-baseline --json
```

New experiments default to `guided` mode. Users and agents update the Markdown directly; the PoC does not provide field-level update commands.

### Launch project code

```bash
hiraku launch logreg-baseline -- uv run python train.py --seed 1
```

`hiraku launch`:

1. resolves the experiment and tracking configuration;
2. rejects a dirty Git working tree;
3. records the current commit SHA;
4. injects the experiment ID, commit SHA, backend type, and resolved backend destination into the child environment; and
5. executes the command and propagates its exit status.

The integration environment includes:

```text
HARIKU_EXPERIMENT_ID
HARIKU_GIT_COMMIT
HARIKU_TRACKING_BACKEND
HARIKU_MLFLOW_EXPERIMENT_ID   # MLflow backend
HARIKU_TABLE_PATH             # table backend
```

The configured MLflow tracking URI is exposed through the standard `MLFLOW_TRACKING_URI` variable. Project code persists the injected commit as MLflow's standard `mlflow.source.git.commit` tag. For a table, project code writes it to the required `commit` column.

The launcher is a thin local command wrapper, not a scheduler or job-dispatch system.

### Inspect runs and the Leaderboard

```bash
hiraku runs logreg-baseline
hiraku runs logreg-baseline --sort test/mse --ascending
hiraku runs logreg-baseline --sort accuracy --descending
hiraku runs logreg-baseline --json
```

Without sorting, `hiraku runs` lists all runs. With `--sort`, it renders the experiment's Leaderboard. `--ascending` or `--descending` is required whenever `--sort` is used.

Rows with missing or non-numeric sort values remain visible after ranked rows. Human output is a table. JSON output contains the same flattened run collection so the agent skill can consume it without scraping terminal formatting.

For MLflow, Hiraku flattens available run metadata, parameters, and metrics into this view. For CSV/TSV, it preserves the table's columns.

## Agent skill

`hiraku init` installs one repository-local Agent Skill at `.agents/skills/hiraku/`.

The skill acts as a router for focused workflows that are loaded when required:

- creating and refining an experiment;
- implementing its current plan;
- launching and analyzing runs; and
- performing a bounded autonomous research loop.


## Approval, review and verification protocol

The workflow supports two modes of operation, `guided` and `auto`. In the guided mode, the data scientist explicitly reviews and approves each step, agent generates artifacts or runs code only on explicit request. In auto mode, the agent approaches the task autonomously to implement, run, analyze, and iterate on experiments within the defined protocol and scope, exposing all artifacts for post-hoc human verification.

> The mode in `experiment.md` defines agent behavior, not a security boundary. Enforcement remains the responsibility of Git protections, sandboxing, credentials, and execution infrastructure.

### Guided mode

`guided` is the default. The data scientist directs the work and reviews the relevant artifacts before the agent proceeds through these checkpoints:

1. experiment scope and implementation plan;
2. implementation and proposed commit;
3. experiment launch; and
4. updating findings, and conclusion.

> In guided mode, the agent is instructed to not commit changes or launch experiments without explicit human approval. 

### Auto mode

In `auto` mode, the data scientist approves the scope, evaluation protocol, permitted commands, resource bounds, and stopping conditions before the loop starts. Within those boundaries, the agent may:

- update the implementation plan;
- modify and commit code;
- launch experiments;
- inspect and compare results;
- update findings and next steps; and
- accept/reject previous changes and select the next in-scope iteration.

The agent pauses for approval before changing scope, evaluation, permitted commands, resource bounds, permissions, or mode. Merging into a protected branch always requires explicit human approval.

Both modes use the same data model. They differ only in when the human reviews and approves actions.

## Installation

For local development, the intended workflow is:

```bash
git clone https://github.com/SwissDataScienceCenter/hiraku.git
cd hiraku
uv sync
uv run hiraku --help
```

To install `hiraku` from a checkout onto `PATH` without activating its virtual environment:

```bash
uv tool install .
hiraku --help
```


## Roadmap

> Features listed here are considered out-of-scope for the proof of concept, but should be taken into account for later iteration.

- Github/GitLab pull request integration for reviewing and approving experiments.
- The "outer loop" of brainstorming or autonomously creating several experiment scopes.
- Improving the "inner loop" by defining exploration strategies.
- Capturing or standardizing agent conversations, traces, chain-of-thought, or tool-call histories.
- Enforcing approval policies and integration with sandboxing.
- A dashboard or other graphical interface.
- Visualizing lineage inferred from git DAG.
- Integration of named entrypoints, launcher and HPO (check if existing tools could be used).
- Cross-experiment Leaderboards
- Additional run-tracking adapters (w&b);

## Out of scope

> Features listed here are not considered part of the Hiraku framework.

- Versioning of datasets, benchmarks, environments, or evaluation protocols is up to the user; these can be specified in code or referenced in agent instructions.


## Appendix: A naming framework for Renku AI

> The proposed naming framework came up in Chatbot interactions. It simply notes a seemingly fitting analogy between Renku-related terminology and an agentic research framework; it has been neither verified with a Japanese speaker, nor is it intended or approved for broader use. 

**Renku** — the Japanese tradition of collaborative linked verse — is proposed as a naming framework for a family of research tools. Each name draws first from its role in a Renku sequence, and from there maps onto a stage or function of research:

1. Hokku (発句) — the opening verse. It establishes the setting, atmosphere, and initial frame of the sequence. More broadly, it represents initiation and context-setting: the contribution that creates the conditions for everything that follows.
2. Wakiku (脇句) — the second verse, which closely responds to and supports the hokku. It develops the opening rather than immediately departing from it. More broadly, it represents reception, grounding, and exploration: taking an initial frame and examining what is present within it.
3. Daisan (第三) — literally “the third.” It is the first verse to create a significant shift away from the opening pair, linking to what came immediately before while introducing greater distance from the original framing. More broadly, it represents critical distance, differentiation, and redirection.
4. Hiraku (平句) — the ordinary or regular verses that make up most of the body of a renku. They are where the sequence develops, moves across subjects, changes perspective, and opens new directions. More broadly, hiraku represents the generative middle: investigation, iteration, experimentation, and sustained development.
5. Ageku (挙句) — the concluding verse. It brings the sequence to a satisfying close without necessarily summarizing or exhausting everything that came before. More broadly, it represents completion and release: bringing a sequence to an end while leaving space for what may follow.

Together, the proposed naming convention frames research as a sequence of **linked contributions that establish, explore, challenge, develop, and communicate ideas**.


|Component|Role|Renku principle|
|---|---|---|
|**Hokku**|Knowledge and literature|Establish|
|**Wakiku**|Data exploration|Explore|
|**Daisan**|Review and verification|Challenge|
|**Hiraku**|AI-assisted research tooling|Investigate|
|**Ageku**|Papers, presentations and posters|Communicate|