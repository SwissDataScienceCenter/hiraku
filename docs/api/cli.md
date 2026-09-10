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

Initialize from an arbitrary local project overlay with `--template`:

```bash
hiraku init --template ./path/to/template
```

Packaged examples use `--example NAME`. For example, start with the minimal
local evaluation project using:

```bash
hiraku init --example minimal
```

Each example copies only its bundled
`src/` subtree; sibling documentation and metadata are not part of the project:

```text
resources/examples/<name>/
├── README.md
├── example.yml
└── src/                 # copied into the project root
```

The two options are mutually exclusive. Templates may replace files installed
by the same initialization, but initialization refuses to overwrite files that
already existed in the target. Explicit options such as `--tracking-backend`
take precedence over values from a template-provided `hiraku.yml`.
After successful initialization, the command lists every file and directory it
created before printing the final project location.

Use `hiraku --help` or the generated command sections below for all options.
The command reference is generated from Hiraku's Click command tree.

::: mkdocs-click
    :module: hiraku.cli
    :command: main
    :prog_name: hiraku
    :depth: 1
    :style: table
    :list_subcommands: true
