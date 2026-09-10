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

Record `show` commands accept `--field FIELD` when only one top-level YAML front
matter value is needed. Scalars are printed directly, while lists and mappings
use compact JSON. Add `--json` to encode the selected value as JSON.

```bash
hiraku task show heart-disease --field goal
hiraku experiment show logistic-baseline --field hypothesis
hiraku program show autoresearch --field time_budget
hiraku program show autoresearch --field time_budget --json
```

This option is available for task, experiment, program, context, and action
records. Requesting a field that does not exist returns an error.

::: mkdocs-click
    :module: hiraku.cli
    :command: main
    :prog_name: hiraku
    :depth: 1
    :style: table
    :list_subcommands: true
