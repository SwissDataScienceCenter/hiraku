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

`hiraku type list` lists project and bundled record types, with project overrides
applied. `hiraku type show <id>` displays the resolved definition and Markdown
description. Both commands accept `--json`. Define types in
`hiraku/types/<id>.md` using `id`, `initial_status`, and `lifecycle` front matter.
Types have no transition graph; individual actions declare their endpoints.

`hiraku agent task draft -m "..."` drafts and activates a new root task. It does
not use the active record as a parent. `hiraku agent experiment draft` creates
one experiment under the active or selected task/experiment; `-m` adds a
researcher-supplied idea to the design prompt.

The experiment draft reply contains `id`, `kind`, and `body`; Hiraku stores
the brief in the record's Markdown body. Task drafting uses
`requires_prompt: true` to require `-m`; action templates render `{{state}}` and
`{{instruction}}`.

`hiraku agent experiment draft -m "..."` creates a draft experiment under the
active task or experiment record. For creation, `--record ID` selects its parent;
for implementation it selects the experiment to change. `implement` requires a
clean working tree, commits the new work, binds its hash, and sets
`status: implemented`.
Pass `--provider`, `--model`, or `--reasoning-effort` to override project agent
defaults. Bundled actions are resolved at runtime; project actions with the same
type and name override them. The result reports the resolved action origin.

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
