# Essential concepts

Hiraku keeps the research description close to the code that evaluates it.

| Concept | Meaning |
| --- | --- |
| **Task** | The research goal, question, metrics, and permitted entrypoints. |
| **Experiment** | One concrete approach and its hypothesis. It can be bound to a source commit. |
| **Run** | One execution of an experiment, with its metrics, parameters, and provenance. |
| **Entrypoint** | A named command that Hiraku can execute. |

A proposal is an Experiment that does not have a committed source yet. Once an
Experiment is bound to a commit, Hiraku can launch it and associate Runs with
that exact source state.

The task record describes *what to investigate*. The experiment record describes
*which approach to try*. The run describes *what happened when it ran*.

Runs are read from the tracking backend; the training program must log them.
Hiraku does not create a run record just because a command executes.

See the [Python API](api/index.md) for validated model fields and the [CLI
reference](api/cli.md) for exact command options.
