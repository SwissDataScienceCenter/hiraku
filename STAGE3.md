# Stage 3 Implementation Plan: Skill Refinement, Documentation, and Remote MLflow

## 1. Purpose

Stage 3 turns the Stage 1 and Stage 2 proof of concept into a workflow that a new user or agent can follow reliably without reconstructing conventions from the main README.

The stage has three outcomes:

1. a concise repository-local Hiraku skill that routes agents into focused research and review workflows;
2. refined guided and autonomous task programs plus consistent, task-neutral examples and user documentation; and
3. remote MLflow tracking support alongside the existing table and local SQLite backends.

Stage 3 does not add the dashboard planned for Stage 4, new tracking providers, schedulers, hyperparameter optimization, cross-task listings, or enforcement of agent permissions.

## 2. Compatibility baseline

Stage 3 builds on the completed Stage 1 and Stage 2 contracts and must preserve them unless this plan explicitly changes an interface:

- task, experiment, launch, run, branch, lineage, and review commands retain their current CLI shape;
- table tracking remains the default and does not require MLflow;
- local MLflow continues to default to the project-local SQLite database;
- remote review operations remain explicit, never approve or merge a review, and never push without authorization;
- exact commits remain stored in records and JSON while human output uses abbreviated hashes;
- guided workflows preserve approval checkpoints;
- autonomous workflows operate only within user-approved scope, permissions, resources, and stopping conditions;
- experiment acceptance and task-branch integration remain normal Git operations rather than implicit Hiraku mutations; and
- the dashboard and graphical lineage views remain deferred to Stage 4.

Before implementing Stage 3, run the complete existing suite with the MLflow extra enabled and treat it as the regression boundary.

## 3. Deliverables

Stage 3 delivers:

- one repository-local router skill installed by `hiraku init`;
- focused research and review workflow instructions loaded by that router;
- revised bundled `guided` and `auto` programs;
- a task-neutral main README workflow;
- a heart-disease autonomous research example;
- `docs/quickstart.md`;
- `docs/tutorial.md`;
- `docs/advanced.md`;
- remote MLflow tracking URI support;
- updated example and reference documentation; and
- unit, integration, packaging, structural, and documentation tests.

## 4. Refine the repository-local skill architecture

Keep `.agents/skills/hiraku/SKILL.md` as the single entrypoint installed by `hiraku init`. Make it a short router rather than a complete workflow manual.

The router should:

- explain the task, experiment, run, Git provenance, tracking, and review concepts briefly;
- inspect the current repository and task context before selecting a workflow;
- route experiment planning, implementation, launch, analysis, conclusion, and decision work to the research workflow;
- route task synthesis, changelog updates, publication, and PR/MR work to the review workflow;
- keep common invariants in one place, including exact-commit binding, structured JSON consumption, program approval boundaries, backend neutrality, and prohibition on implicit merges or pushes;
- avoid loading both detailed workflows when only one is relevant; and
- point to task-local `program.md` as the authoritative workflow and permission policy.

Package the focused workflows as files beneath the installed Hiraku skill, for example:

```text
.agents/skills/hiraku/
  SKILL.md
  references/
    research.md
    review.md
```

Use resource-copying behavior already provided by `hiraku init`, so new projects receive the complete skill and existing customized skill files are not silently overwritten.

Acceptance criteria:

- `SKILL.md` is a concise router with unambiguous research/review selection rules;
- each detailed workflow can be read independently after routing;
- common safety and provenance rules are not contradicted by either workflow;
- installed resources are included in the built package; and
- re-running `hiraku init` remains idempotent and preserves customized files under the existing collision policy.

## 5. Define the research workflow

Create a focused research workflow covering one experiment lifecycle from intent through conclusion.

The workflow should:

1. resolve the current task and read `task.md`, `program.md`, relevant experiment records, entrypoints, and current runs;
2. verify that the task goal, scope, evaluation protocol, declared metrics and directions, permitted commands, resource bounds, permissions, and stopping conditions are sufficient for the requested work;
3. stop for clarification only when missing information materially changes the experiment or violates the task program;
4. formulate one experiment with an ID, summary, hypothesis, concrete entrypoint invocation, and—when applicable—a branch;
5. respect guided approval checkpoints and autonomous boundaries;
6. implement the experiment only on the appropriate task or experiment branch;
7. commit the coherent implementation and bind the exact commit;
8. launch only the invocation declared by the experiment;
9. inspect runs through `hiraku runs --json`, including provenance and commit-mismatch warnings;
10. compare the task-declared primary and secondary metrics using their declared directions;
11. record status, decision, and conclusion in the experiment; and
12. integrate accepted work or discard rejected work only as directed by `program.md` and normal Git.

The workflow must distinguish:

- scientific acceptance from Git containment in the task branch;
- reproducible committed launches from working-tree debugging launches;
- a failed scientific idea from a correctable implementation error;
- table and MLflow storage details from the backend-neutral research lifecycle; and
- an individual experiment conclusion from the task-level reviewed synthesis.

Acceptance criteria:

- the workflow covers linear, revert/reset-based, and branching task programs without assuming one strategy;
- it consumes JSON rather than scraping human tables;
- it never edits backend-managed run data;
- it does not change task scope, evaluation, permissions, resources, or stopping conditions without approval; and
- it produces complete experiment records that can be reviewed later without conversation history.

## 6. Define the review workflow

Create a focused review workflow for turning task-local evidence into the canonical `task.md` and optional PR/MR.

The workflow should:

1. resolve the task from an explicit selector or current task/experiment branch;
2. inspect task metadata, all experiment records, run summaries, metric rankings, lineage, containment, commit mismatches, and existing review identity;
3. identify unresolved or contradictory states, including accepted experiments not contained in the task branch and recorded conclusions unsupported by runs;
4. synthesize reviewed findings, conclusions, decisions, open questions, and next steps in `task.md`;
5. append a concise `## Changelog` entry describing the review update;
6. preserve the required front matter, declared metrics, provider-specific `pr_id` or `mr_id`, and user-written material not superseded by the review;
7. preview the exact task document and publication action before external mutation when required by the task program;
8. push only with explicit authorization; and
9. create or update the configured GitHub pull request or GitLab merge request without approving or merging it.

Acceptance criteria:

- `task.md` remains the complete, verbatim remote review description;
- the workflow works from task and experiment branches under the Stage 2 restrictions;
- PR/MR identity and provider mismatches are handled explicitly;
- no push, review creation, review update, approval, or merge occurs outside the applicable authorization boundary; and
- the resulting task document is understandable without access to agent conversation history.

## 7. Refine bundled guided and autonomous programs

Rewrite the default `guided/program.md` and `auto/program.md` for clarity and operational completeness while preserving their different authority models.

The guided program should provide concise checkpoints for:

1. task scope and evaluation approval;
2. experiment intent and branch choice;
3. implementation and proposed source commit;
4. launch approval;
5. run analysis and experiment conclusion; and
6. task synthesis and optional review publication.

The autonomous program should require the human to define scope, evaluation, permitted commands, resource limits, permissions, and stopping conditions before the loop. Within those bounds it should define a complete repeatable experiment loop, conditions that require pausing, and a final task-synthesis step.

Both programs should:

- use current Hiraku commands and terminology;
- distinguish semantic decisions from Git integration;
- support table, local MLflow, and remote MLflow without backend-specific research logic;
- tell the agent never to edit tracking data directly;
- direct agents to the task-declared metrics and directions;
- use exact commits for reproducible runs; and
- keep protected-branch merges and external publication subject to explicit authorization.

Acceptance criteria:

- the programs are shorter and clearer than duplicating the full skill;
- guided and autonomous authority boundaries remain visibly different;
- each program contains a complete start-to-finish workflow rather than isolated cautions; and
- structural tests assert critical checkpoints, boundaries, and current CLI commands.

## 8. Make README examples task-neutral and consistent

Revise examples in the main README so the task defines the scientific goal and evaluation protocol without prematurely fixing the model family. Use experiment examples—such as logistic regression and random forest—to introduce concrete candidate strategies.

Normalize example terminology and commands across the conceptual overview, CLI reference, quick workflow, and Stage summaries:

- use the same representative task ID and metrics where continuity helps;
- distinguish task goal from experiment hypothesis;
- use the linear workflow in introductory material;
- reserve branching and review integration for explicitly advanced sections;
- show declared metrics and directions in task front matter;
- keep table and MLflow examples backend-neutral unless explaining configuration; and
- ensure every launch invocation is declared in the corresponding experiment example.

Acceptance criteria:

- no introductory task example constrains the answer to a specific model family;
- commands agree with the implemented CLI;
- task, experiment, and run responsibilities remain distinct; and
- links point readers to the appropriate detailed document instead of duplicating long procedures.

### 8.1 README example contract

Use one consistent heart-disease storyline as the primary README example. The task is strategy-neutral:

```yaml
id: heart-disease
summary: Improve heart-disease risk prediction
goal: Minimize validation log loss on the checksum-pinned Cleveland cohort.
metrics:
  - name: val/log_loss
    direction: minimize
  - name: val/accuracy
    direction: maximize
branch: hiraku/task/heart-disease
```

The task fixes the dataset, split, preprocessing constraints, and evaluation metrics without prescribing a model family. Concrete strategies appear only as experiments:

1. `logistic-baseline` tests regularized logistic regression as the minimal linear workflow;
2. `random-forest` tests whether nonlinear feature interactions improve validation performance and demonstrates run comparison; and
3. `stronger-regularization`, or an equivalently small refinement, demonstrates experiment branching, lineage, acceptance, and task-branch containment.

Use this continuous narrative across the README: define the task, run the logistic baseline, compare the random forest, optionally refine on an experiment branch, synthesize `task.md`, and create or update a PR/MR.

Keep the supporting examples distinct:

- `examples/smoke` is the shortest installation and tracking-contract check, with one deterministic run and no modeling narrative;
- the guided heart-disease template is the main README and linear-tutorial example;
- `heart-disease-auto` demonstrates a bounded CPU-feasible autonomous loop over the same fixed data and evaluation protocol; and
- `examples/autoresearch` demonstrates the long-running single-GPU autonomous loop with `revert`, `reset`, and `branch` history modes.

README section ownership:

- core concepts, task records, experiment records, entrypoints, the minimal workflow, run comparison, branching, acceptance, and review integration use the heart-disease storyline;
- tracking configuration shows the same heart-disease experiments under table, local SQLite MLflow, and remote MLflow;
- installation verification links to smoke; and
- autonomous research summarizes and links to heart-disease-auto and autoresearch rather than mixing those loops into the introductory walkthrough.

Acceptance criteria:

- task-level examples never impose logistic regression or random forest as part of the task goal;
- each experiment has a concrete hypothesis and a declared invocation matching the example launch configuration;
- examples do not change identities or metrics between README sections;
- smoke, guided heart disease, heart-disease-auto, and autoresearch each have one clearly stated purpose; and
- README, quickstart, tutorial, advanced guide, and example READMEs follow this same division of responsibilities.

## 9. Add the documentation set

Create a `docs/` directory with three audience-oriented documents.

### 9.1 `docs/quickstart.md`

Provide the shortest complete path from an existing Git repository to one reviewed experiment:

1. install Hiraku;
2. run `hiraku init`;
3. create and enter a guided task;
4. fill task goal, evaluation, and metrics;
5. create one experiment;
6. implement, commit, and bind it;
7. launch a declared entrypoint;
8. inspect runs;
9. conclude the experiment; and
10. update `task.md`.

Use only the linear workflow. Link to the tutorial for explanations and advanced documentation for branches and reviews.

### 9.2 `docs/tutorial.md`

Provide a detailed linear-workflow walkthrough consistent with the README examples. Explain:

- the role and lifecycle of each artifact;
- task setup and metric declarations;
- entrypoint definition and experiment invocation declarations;
- implementation commits and exact binding;
- table tracking, local SQLite MLflow, compact run inspection, metric selection, verbose output, and JSON;
- experiment status, decision, and conclusion;
- task synthesis; and
- common failure modes such as binding a commit before `launch.yml` exists, mismatched commits, undeclared launch invocations, and missing provenance.

Do not introduce experiment branches or PR/MR operations in the tutorial.

### 9.3 `docs/advanced.md`

Document:

- experiment branches, `--from`, sibling experiments, and backtracking;
- lineage derived from Git ancestry;
- the `*` containment marker and its limitations for squash merges and cherry-picks;
- accepting work into the task branch with normal Git;
- GitHub PR and GitLab MR configuration, preview, push authorization, creation, and update;
- local and remote MLflow configuration;
- provenance diagnostics and MLflow `--all`;
- custom templates and programs; and
- operational boundaries that Hiraku documents but does not enforce.

Acceptance criteria for documentation:

- all three files are linked from the README;
- command snippets are checked against the current parser or exercised in documentation tests where practical;
- quickstart and tutorial remain linear-only;
- advanced documentation owns branch and review complexity; and
- terminology, paths, configuration fields, and examples agree across all four documents.

## 10. Add a heart-disease autonomous research flow

Extend the heart-disease example with an autonomous workflow while retaining the existing guided comparison.

The autonomous flow should reuse:

- the checksum-pinned Cleveland dataset;
- the fixed preprocessing and validation protocol;
- declared `val/log_loss` and `val/accuracy` metrics;
- the example's table/MLflow-compatible tracking writer; and
- the same reproducibility and provenance requirements.

It should demonstrate:

- explicit autonomous scope, permitted edits and commands, resource bounds, and stopping conditions;
- baseline establishment;
- repeated experiment creation, implementation, binding, launch, comparison, and conclusion;
- selection using validation log loss as the primary minimized metric and accuracy as secondary evidence;
- handling of rejected or crashed candidates; and
- final task synthesis for human review.

Package this as a second complete `heart-disease-auto` template alongside the existing guided `heart-disease` template, with updated example README instructions. Permit edits to the training implementation and model configurations using existing dependencies only. Keep the data, checksum, preprocessing, split, metrics, and evaluation fixed, and require an explicit iteration or wall-clock bound before the loop starts.

## 11. Add remote MLflow tracking URI support

Generalize MLflow configuration so the backend can use either the current local SQLite database or a remote tracking URI.

The implementation should:

- preserve `tracking_backend: mlflow` and the existing local SQLite default;
- add an explicit project configuration field for a remote tracking URI;
- make local database and remote URI configuration mutually exclusive;
- validate configuration early and identify the conflicting or invalid field;
- construct the absolute local SQLite URI exactly as Stage 2 does when no remote URI is configured;
- pass the selected URI through `HIRAKU_MLFLOW_TRACKING_URI` and `MLFLOW_TRACKING_URI` to launched code;
- keep the task's `mlflow_experiment` requirement;
- use the selected URI for `hiraku runs` and experiment enrichment;
- rely on standard MLflow environment variables or runtime configuration for authentication rather than storing secrets in `.hiraku.yml`;
- avoid logging credentials or embedding them in human diagnostics;
- retain lazy MLflow imports and the optional dependency boundary; and
- produce actionable connectivity and authentication errors without falling back silently to local storage.

Remote support is tracking-client support only. Hiraku does not provision, start, migrate, administer, or secure an MLflow server, and does not add model-registry behavior.

Acceptance criteria:

- existing table and local SQLite configurations remain compatible;
- remote and local configuration cannot be active simultaneously;
- launches and reads select the same configured store;
- credentials remain outside tracked Hiraku configuration and output;
- mocked integration tests cover URI injection, run retrieval, connectivity failures, and authentication failures without requiring a public service; and
- an optional manual test documents validation against a disposable MLflow server.

Add `mlflow_tracking_uri` for remote `http://` and `https://` tracking servers. Make it mutually exclusive with `mlflow_tracking_db`; pass it through unchanged after validation; use standard MLflow environment variables and credential mechanisms; and never fall back to local SQLite after a remote error.

## 12. Verification strategy

Add or update tests in these layers.

### Unit tests

- skill routing rules and required shared invariants;
- research and review workflow structural requirements;
- guided and autonomous program checkpoints;
- remote MLflow configuration validation, mutual exclusion, and URI selection;
- credential redaction in errors and diagnostics; and
- documentation links and internal paths.

### Integration tests

- `hiraku init` installs the router and both focused workflow resources;
- repeated initialization preserves customized skill files;
- local SQLite MLflow behavior remains unchanged;
- remote MLflow client construction and launch environment use the configured URI;
- remote errors do not trigger local fallback;
- heart-disease guided and autonomous template creation; and
- README and documentation command sequences that do not require external services or GPU execution.

### End-to-end and manual tests

- complete linear quickstart using the smoke or heart-disease example;
- complete detailed tutorial path using table tracking;
- local SQLite variant of the same run-inspection workflow;
- autonomous heart-disease CPU-feasible flow within a bounded test configuration;
- experiment branch acceptance and rejection from the advanced guide;
- fake GitHub/GitLab review create and update flows; and
- optional remote MLflow validation against a disposable authenticated or unauthenticated server.

### Release checks

- Ruff formatting and linting;
- complete default test suite without MLflow installed;
- complete suite with the MLflow extra;
- package build and installed-resource inspection;
- supported Python versions from CI;
- no secrets, generated databases, run tables, logs, datasets, or virtual environments included in packages or fixtures; and
- link and terminology review across README, docs, skills, programs, and examples.

## 13. Delivery sequence

Implement Stage 3 in this order:

1. capture the existing suite as the compatibility baseline;
2. incorporate the resolved heart-disease-auto and remote MLflow contracts into the specification;
3. establish the skill router and focused workflow resource layout;
4. implement and test the research workflow;
5. implement and test the review workflow;
6. refine and test guided and autonomous programs;
7. settle and implement the remote MLflow configuration contract;
8. add the heart-disease autonomous flow;
9. normalize README examples;
10. write quickstart, tutorial, and advanced documentation;
11. add documentation, integration, packaging, and end-to-end tests; and
12. run release checks and manually verify the representative workflows.

## 14. Stage 3 completion criteria

Stage 3 is complete when:

- a new agent can enter through one concise Hiraku skill and load the correct focused workflow;
- the research workflow reliably handles one full experiment lifecycle;
- the review workflow reliably produces and optionally publishes the canonical task review;
- guided and autonomous programs are clear, current, and behaviorally distinct;
- README examples are task-neutral and consistent;
- quickstart, linear tutorial, and advanced branching/review documentation are complete and cross-linked;
- the heart-disease example demonstrates a bounded autonomous research flow;
- remote MLflow runs and inspection work without weakening local SQLite or table behavior;
- optional dependencies and external side effects remain explicit;
- all automated checks pass; and
- both resolved design decisions are represented in implementation, documentation, and tests.
