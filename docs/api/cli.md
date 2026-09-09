# CLI Reference

The CLI is the quickest way to manage tasks, experiments, and runs.

Typical workflow:

```bash
hiraku init
hiraku task create <task-id>
hiraku exp create <experiment-id>
hiraku launch <experiment-id>:<entrypoint>
hiraku run list
```

Use `hiraku --help` or the generated command sections below for all options.
The command reference is generated from Hiraku's Click command tree.

::: mkdocs-click
    :module: hiraku.cli
    :command: main
    :prog_name: hiraku
    :depth: 1
    :style: table
    :list_subcommands: true
