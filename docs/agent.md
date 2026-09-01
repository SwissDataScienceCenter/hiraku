# Hiraku Agent
### Agent integration

Hiraku programs define agent instructions for research workflows. Each `program.md` records explicit boundaries and permissions, resource limits, stopping conditions, and natural-language instructions such as hypothesis generation and user confirmation. In the simplest setting, the program is interpreted by the LLM without system-enforced boundaries. Later integration with a harness or sandbox may enforce these constraints.

Programs are first-class records managed by the Core API and CLI:

```bash
hiraku program create my-program
hiraku program list
hiraku program show interactive
hiraku program set autoresearch
hiraku program update my-program --summary "Custom bounded workflow"
hiraku program delete my-program
```

`create` makes the new program active unless `--no-set` is supplied. `set` updates `program_id` in `.hiraku/context.yml`; `update` modifies the program record. Direct editing remains supported. Deleting a program that is active or referenced by a task is rejected unless explicitly forced.

Programs take precedence in this order:
```
--program <program-id>
HIRAKU_PROGRAM_ID
.hiraku/context.yml
the task record's `program` field
the repository configuration's `program` field
interactive
```

Programs are saved in the Hiraku folder:
```
.hiraku/programs/<program-id>/program.md
```

By default, Hiraku ships with two workflows:

- **interactive:** A step-by-step program where the data scientist explicitly approves each checkpoint and reviews artifacts before proceeding.
- **autoresearch:** A bounded autonomous loop with explicit permissions, resource limits, and stopping conditions under which the agent implements, runs, analyzes, and iterates, exposing all artifacts for post-hoc human verification.


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

The hiraku agent invokes actions that are defined in `hiraku/actions/<entity>/<action-id>.md`. E.g., `hiraku auto experiment create` invokes `hiraku/actions/experiment/create/action.md`. The action defines the agent's instructions, permissions, and boundaries. The user can define custom actions for their own workflows. Action combine deterministic hooks with natural-language instructions.

```bash
hiraku-auto experiment create heart-disease:logistic-baseline --summary "Logistic regression baseline" --hypothesis "Regularized logistic regression provides a calibrated baseline" --entrypoint logistic --agent pi --program interactive
```

```bash
hiraku-auto experiment create heart-disease:logistic-baseline --summary "Logistic regression baseline" --hypothesis "Regularized logistic regression provides a calibrated baseline" --entrypoint logistic --agent pi --program autoresearch
```



#### Hiraku Harness
   > ToDo: Create Pi extension that explicitly parses programs and enforces the workflow and boundaries.

