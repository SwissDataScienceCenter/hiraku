# Hiraku Agent
### Agent integration

Hiraku programs define agent instructions for research workflows. Each `program.md` records explicit boundaries and permissions, resource limits, stopping conditions, and natural-language instructions such as hypothesis generation and user confirmation. In the simplest setting, the program is interpreted by the LLM without system-enforced boundaries. Later integration with a harness or sandbox may enforce these constraints.

Programs are first-class records managed by the Core API and CLI:

```bash
hiraku program create my-program
hiraku program list
hiraku program show interactive
hiraku program set autoresearch
hiraku program update my-program --summary "Custom workflow"
hiraku program delete my-program
```

`create` makes the new program active unless `--no-set` is supplied. `set`
updates `program_id` in `hiraku.yml`; `update` modifies the Markdown program
record. Direct editing remains supported. Deleting a program that is active or
referenced by a task is rejected unless explicitly forced.

Programs take precedence in this order:
```
--program <program-id>
HIRAKU_PROGRAM_ID
hiraku.yml
the task record's `program` field
the repository configuration's `program` field
interactive
```

Programs are saved in the Hiraku folder:
```
hiraku/programs/{program_id}/program.md
```

By default, Hiraku ships with two workflows:

- **interactive:** A step-by-step program where the data scientist explicitly approves each checkpoint and reviews artifacts before proceeding.
- **autoresearch:** An autonomous loop with explicit permissions, resource limits, and stopping conditions under which the agent implements, runs, analyzes, and iterates, exposing all artifacts for post-hoc human verification.


**Example**
> ToDo: Define agent integration and core fields. Right now, only id is defined, everything else is interpreted as skill. Later we can pass precise instructions to a custom Harness or sandbox.

```markdown
---
id: interactive
summary: A interactive, step-by-step workflow with explicit human approval and review.
gates:
  - git commit
  - git push
allowed:
  - hiraku task show
  - hiraku experiment create
  - hiraku experiment update
  - hiraku launch
  - hiraku runs
disallowed:
  - hiraku experiment delete
  - hiraku experiment sign
  - hiraku task conclude
branch-mode: linear  # restricts experiments to the task branch; no branching or merging is allowed.
limits:
  time: 5 min
  memory: 16 GB
  gpu: 16 GB
  episodes: 1000
---
# Interactive workflow 
<ToDo: Fill example with agent instructions.>

```

#### Agent Skill

The simplest way to use Hiraku is with a Coding harness, e.g. Codex, Claude, Pi, ..., and invoke the `/hiraku` kill with natural language instructions. The instructs the agent to follow Hiraku workflows and read the relevant files.

`hiraku init` installs one repository-local Agent Skill at `.agents/skills/hiraku/`.

The skill acts as a router for focused workflows that are loaded when required:

- creating and refining a task;
- executing the task's program within its approved boundaries;
- creating, refining, binding, and reviewing local experiment records;
- launching and analyzing runs; and
- performing a bounded autonomous research loop.


#### Hiraku Agent

> ToDo: `hiraku-auto` or `hiraku-agent` ?

The hiraku agent invokes actions defined at
`hiraku/actions/{entity}/{action_id}/action.md`. For example,
`hiraku auto experiment create` invokes
`hiraku/actions/experiment/create/action.md`. The action defines the agent's
instructions, permissions, and boundaries. Users can define custom actions for
their own workflows. Actions combine deterministic hooks with natural-language
instructions.

```bash
hiraku-auto experiment create heart-disease:logistic-baseline --summary "Logistic regression baseline" --hypothesis "Regularized logistic regression provides a calibrated baseline" --entrypoint logistic --agent pi --program interactive
```

```bash
hiraku-auto experiment create heart-disease:logistic-baseline --summary "Logistic regression baseline" --hypothesis "Regularized logistic regression provides a calibrated baseline" --entrypoint logistic --agent pi --program autoresearch
```

##### Action hooks

An action may provide deterministic behavior in a Python module stored locally
with its instructions:

```text
hiraku/actions/experiment/implement/
├── action.md
└── hooks.py
```

The action record opts into the module explicitly. A `hooks.py` file that is
not declared must not be loaded or executed.

```markdown
---
id: implement
entity: experiment
hooks: hooks.py
---
# Implement an experiment

Implement the selected proposal and expose the changes for review.
```

The initial hook contract consists of five optional synchronous functions:

```python
def precondition(context: ReadonlyActionContext) -> ConditionResult: ...
def before(context: ActionContext) -> None: ...
def after(context: ActionContext, result: ActionResult) -> None: ...
def postcondition(
    context: ReadonlyActionContext,
    result: ActionResult,
) -> ConditionResult: ...
def cleanup(
    context: ActionContext,
    result: ActionResult | None,
    error: BaseException | None,
) -> None: ...
```

Conditions inspect state and return a structured pass or failure result.
`before` and `after` implement deterministic side effects, while `cleanup` is
always called for best-effort finalization. The action lifecycle is:

```text
resolve action, target, arguments, program, and context
→ load and validate hooks
→ precondition
→ before
→ invoke agent
→ after
→ postcondition
→ cleanup
```

`after` and `postcondition` run only when agent invocation succeeds. `cleanup`
runs after every attempt, including precondition, hook, and agent failures. A
failed condition is expected inapplicability and is reported separately from a
hook error. A cleanup error is reported without hiding the original failure.

The runtime constructs a single invocation-scoped context rather than
injecting a changing list of positional objects. It contains the resolved
action, program, project configuration, task, experiment, referenced context
records, invocation arguments, and initialized Hiraku services such as Git,
tracking, event recording, and command execution. Convenience methods such as
`require_task()` and `require_experiment()` provide clear failures when an
action requires a particular scope. `ActionResult` separately records the
agent outcome, summary, changed files, structured output, and provider
metadata.

For example:

```python
from hiraku.agent.hooks import (
    ActionContext,
    ActionResult,
    ConditionResult,
    ReadonlyActionContext,
)


def precondition(context: ReadonlyActionContext) -> ConditionResult:
    experiment = context.require_experiment()
    if experiment.commit is not None:
        return ConditionResult.fail(
            "Experiment is already implemented.",
            commit=experiment.commit,
        )
    return ConditionResult.pass_()


def after(context: ActionContext, result: ActionResult) -> None:
    context.services.events.record(
        "implementation-completed",
        changed_files=result.changed_files,
    )


def postcondition(
    context: ReadonlyActionContext,
    result: ActionResult,
) -> ConditionResult:
    if not result.changed_files:
        return ConditionResult.fail("The agent made no file changes.")
    return ConditionResult.pass_()
```

The hook module is loaded directly from its declared path under a unique module
name; its directory is not added to `sys.path`. The runtime validates all hook
signatures before running any hook. Async hooks, hook classes, decorators, and
multiple functions per phase are outside the initial contract.

In-process Python hooks are trusted project extension code, not a security
boundary. Invoking an action authorizes its declared hook module in the same
sense as running other repository code. Program permissions can restrict
whether hooks may run, but actual isolation belongs to a future worker process
or sandbox. Read-only context types communicate and help enforce that
conditions should not mutate state, but they do not make untrusted Python safe.



#### Hiraku Harness
   > ToDo: Create Pi extension that explicitly parses programs and enforces the workflow and boundaries.
