# CLI Reference

The CLI manages research records, launches, and tracked runs.

Typical workflow:

```bash
hiraku init
hiraku record create <root-id>
hiraku record create <child-id> --parent <root-id>
hiraku launch <entrypoint> --record <child-id>
hiraku runs --record <record-id>
```

`hiraku record list` includes each record's local `status` in its table.

Initialization uses the following destinations, creating missing directories:

| Command | Destination |
| --- | --- |
| `hiraku init` | Current directory |
| `hiraku init my-project` | `./my-project` |
| `hiraku init --example minimal` | `./minimal` |
| `hiraku init my-project --example minimal` | `./my-project` |
| `hiraku init . --example minimal` | Current directory |

An explicit path always takes precedence. Plain initialization supports existing
directories and preserves unrelated files. Repeating plain `hiraku init` reports
that the project is already initialized and leaves every file unchanged; it does
not reinstall missing resources. Initialization with an example, template, or
configuration options still rejects an already initialized project.

`hiraku init` still requires its target to be a Git repository root. Use
`hiraku init PATH --ignore-git` to intentionally initialize a nested or non-Git
location. Python initialization is permissive by default; callers can request
the CLI policy with `ignore_git=False`. For example, create an example directory
without first setting up Git with `hiraku init --example minimal --ignore-git`.
Otherwise, initialize Git at the intended destination first.

Initialize from an arbitrary local project overlay with `--template`:

```bash
hiraku init --template ./path/to/template
```

Packaged examples use `--example NAME`. For example, start with the minimal
local evaluation project using:

```bash
hiraku init --example minimal --ignore-git
```

Each example copies only its bundled
`src/` subtree by default. Add `--with-results` to apply its optional `results/`
overlay with sample records and tracked runs:

```bash
hiraku init --example heart-disease --with-results --ignore-git
```

Results may override base files and configuration keys, but neither layer
overwrites pre-existing project files. Explicit CLI options take precedence.
The flag requires a packaged example with a `results/` directory.
Use `--with-generator` to copy an example's optional `generate_results.py` for
running later, or `--generate` to execute it immediately after initialization.
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

The two options are mutually exclusive. Templates may replace files installed
by the same initialization, but initialization refuses to overwrite files that
already existed in the target. Explicit options such as `--tracking-backend`
take precedence over values from a template-provided `hiraku.yml`.
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
