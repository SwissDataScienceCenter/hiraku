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

Actions use `hiraku agent <action-id> [type]`. The bundled `create` action
accepts task and experiment and has no default type:

```bash
hiraku agent create task --id heart-disease -m "Compare predictive models"
hiraku agent create experiment --id gradient-boosting --record heart-disease
hiraku agent implement --record gradient-boosting
hiraku agent implement
```

Task creation requires `-m`, creates a root task, and activates it, ignoring
the active record as a parent. Experiment creation uses the selected or active
task/experiment as parent; `-m` optionally adds research instructions. Its
reply contains `id`, `kind`, and `body`; Hiraku stores the brief as Markdown.

`--id` is optional for creation. Hiraku validates supplied IDs and uniqueness
before provider execution and assigns the ID itself. Without it, the provider
proposes an ID, which is validated before persistence. Transition actions reject
`--id`. Existing-record actions infer the type from the selected or active
record; an explicit positional type must match it.

Action definitions live at `hiraku/actions/<action-id>/action.md`, with
project-first resolution and bundled fallback. Front matter declares
`accepted_types`, optional `default_type`, `effect`, and `hooks`.
Creation uses `type_prompts` to map types to local Markdown fragments.
Fragment front matter declares `id`, `parent_types`, `requires_prompt`,
and `activate`; its body provides the type-specific output contract.
The common prompt renders `{{type_prompt}}`, `{{state}}`, and
`{{instruction}}`. State and hooks receive the resolved type, supplied ID,
type description, and research context. Hiraku assigns the initial status
from the type definition.

`implement` accepts experiments in `draft` with no bound commit and requires a
clean working tree. Hiraku commits the work, binds its hash, and advances the
status to `implemented`. Provider progress, edited files, deterministic changes,
and attempt logs remain available.

Use `--provider`, `--model`, or `--reasoning-effort` to override project
defaults. `hiraku action list [--type TYPE]` lists resolved actions;
`hiraku action show <action-id>` shows the definition. Bundled actions are
not copied into projects, and results declare their origin. Dataset creation
is deferred; project actions can accept custom types.

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
