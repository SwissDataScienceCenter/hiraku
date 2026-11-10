# CLI Reference

The CLI manages research records, foreground command execution, and tracked runs.

Typical workflow:

```bash
hiraku init
hiraku record create tasks/question --title "Research question" --type task
hiraku record create experiments/baseline --parent tasks/question --type experiment
hiraku run --record experiments/baseline -- python train.py
hiraku runs --record experiments/baseline
```

`record create RECORD_ID` takes the OKF ID: a bundle-relative path without `.md`.
Use `--title` for the optional display title. References and run attribution use
this ID. IDs are derived from placement and never stored in front matter.

`hiraku record list` includes each record's local `state` in its table.
Commands with an optional `--json` output accept `--pretty` alongside it to
render the JSON with Rich. For example, use `hiraku runs --json --pretty`.
Workflow start, sessions, history, status, and resume commands always return
JSON and accept `--pretty` directly.

Initialization uses the following destinations, creating missing directories:

| Command | Destination |
| --- | --- |
| `hiraku init` | Current directory |
| `hiraku init my-project` | `./my-project` |
| `hiraku example create minimal` | `./minimal` |
| `hiraku example create minimal my-project` | `./my-project` |
| `hiraku example create minimal .` | Current directory |

An explicit path always takes precedence. Plain initialization supports existing
directories and preserves unrelated files. Repeating plain `hiraku init` reports
that the project is already initialized and leaves every file unchanged; it does
not reinstall missing resources. Example creation, or initialization with a template or
configuration options, rejects an already initialized project. Both commands
preserve unrelated files in existing destinations and refuse file collisions.

`hiraku init` does not require or invoke Git, and works in nested directories as
well as outside repositories. Add `--git` to initialize the code repository at the
destination. Existing repositories and worktrees are preserved. Repeating plain
initialization with `--git` can add Git to an existing Hiraku project.

Initialize from an arbitrary local project overlay with `--template`:

```bash
hiraku init --template ./path/to/template
```

List available starting points with `hiraku example list`.

Create a project from a bundled example with `hiraku example create EXAMPLE [PATH]`.
The default destination is `./EXAMPLE`. This copies packaged files without
Git history or network access; it does not accept repository URLs. Add `--git`
to initialize the code repository. For example:

```bash
hiraku example create minimal
```

Each example copies only its bundled
`src/` subtree by default. Add `--with-results` to apply its optional `results/`
overlay with sample records and tracked runs:

```bash
hiraku example create heart-disease --with-results
```

Results may override base files and configuration keys, but neither layer
overwrites pre-existing project files. Explicit CLI options take precedence.
The flag requires a packaged example with a `results/` directory.
Use `--with-generator` to copy an example's optional `generate_results.py` for
running later, or `--generate` to execute it immediately after initialization.
Immediate generation uses an isolated temporary Git project and publishes the
completed batch, so the new project does not need an initial commit yet.
Both flags require an example with a generator; `--generate` cannot accompany
`--with-results`. Already initialized projects are rejected. Generation failures retain the new
project and completed runs. `--generate` alone does not copy the script.
Sibling documentation and metadata are not copied:

```text
resources/examples/<name>/
├── README.md
├── example.yml
├── src/                 # copied into the project root
└── results/             # optional overlay with --with-results
```

`init --template` and `example create` are separate commands. Templates may replace files installed
by the same initialization, but initialization refuses to overwrite files that
already existed in the target. Tracking and command defaults belong on the
root record, not in project configuration.
After successful initialization, the command prints the project configuration
location.

Use `hiraku --help` or the generated command sections below for all options.
The command reference is generated from Hiraku's Click command tree.

::: mkdocs-click
    :module: hiraku.cli
    :command: main
    :prog_name: hiraku
    :depth: 1
    :style: table
    :list_subcommands: true
