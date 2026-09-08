# Work with an AI agent

`hiraku init` installs instructions at `.agents/skills/hiraku/SKILL.md`.
Ask your agent to read that file and the active task before working. Skill
discovery varies by agent; the file path works even when no slash-command
menu entry is available.

The bundled skill asks the agent to use Hiraku for records and launches,
Git for source history, and the selected tracking backend for run inspection.

## Start with one reviewed experiment

After completing [project setup](own-project.md), give the agent a concrete
request:

```text
Read .agents/skills/hiraku/SKILL.md and the interactive program.
Work on task my-task. Propose one experiment to improve val/accuracy.
Keep the dataset, validation split, and metric unchanged.
Ask me before editing source, committing, and launching.
Use the evaluate entrypoint and record your findings in the experiment.
```

The agent can inspect the bundled program records with:

```bash
hiraku program show interactive
hiraku program show autoresearch
```

## Bound an autonomous session

Specify allowed changes, maximum experiments and runs, elapsed-time or compute
limits, and stopping conditions in the task/program and your instruction to
the agent. For example: try at most two approaches, one run each, and stop
after 20 minutes or if evaluation fails.

The bundled programs are short instructions. This version does not provide a
CLI-managed autonomous scheduler or enforce those budgets automatically.
The agent must follow the approved boundaries.

Programs describe workflows, actions describe individual operations, and
contexts hold supporting information. These are editable metadata records;
creating one does not execute an agent.

Hiraku validates metadata and supplies execution attribution. Training code
owns metric logging, and the agent interprets results. Unlogged executions
are not automatically represented in the tracking backend.
