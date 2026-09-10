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

`hiraku init` requires its target to be a Git repository root. Use
`hiraku init PATH --ignore-git` to intentionally initialize a nested or non-Git
location. Python initialization is permissive by default; callers can request
the CLI policy with `ignore_git=False`.

Use `hiraku --help` or the generated command sections below for all options.
The command reference is generated from Hiraku's Click command tree.

::: mkdocs-click
    :module: hiraku.cli
    :command: main
    :prog_name: hiraku
    :depth: 1
    :style: table
    :list_subcommands: true
