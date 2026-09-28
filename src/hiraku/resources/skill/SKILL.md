---
name: hiraku
description: Use Hiraku to document research records, launch entrypoints, and inspect tracked runs.
---
# Hiraku

Hiraku keeps research notes, executable definitions, and run attribution in
Markdown records. A record may stand alone or have one parent. Use `parent_id`
for organizational hierarchy and inheritance; use `related_to` to reference
other records without inheriting from them.

## Inspect the project

Start with the active record and relevant history:

```bash
hiraku status --json
hiraku record list --json
hiraku record tree
hiraku record show <record-id> --json
hiraku runs --record <record-id> --json
```

Records are stored at `hiraku/records/{root_id}/{record_id}.md`, including the
root record itself. IDs are unique in a project. The file's YAML front matter
contains structured fields, followed by free-form Markdown. Preserve unknown
fields and the prose when editing.

## Work with records

```bash
hiraku record create <id> --set 'goal=Improve calibration'
hiraku record create <id> --parent <parent-id> --type experiment
hiraku record update <id> --set 'status=implemented'
hiraku record update <id> --unset status
hiraku record set <id>
hiraku record show <id> --raw
hiraku record entrypoints <id>
```

An omitted field inherits from the nearest parent definition. An explicit
`null` clears an inherited value. Named entrypoints merge, with the nearest
definition of each name winning. `id`, `parent_id`, `related_to`, `type`,
`commit`, `status`, and `decision` remain local to the record. `record show`
reports effective values and their sources; `--raw` displays stored values.

Do not treat `related_to` as experimental derivation. A Git commit identifies
source code, while `related_to` only provides a cross reference. Document the
actual relationship in the record's Markdown when it matters.

## Launch and inspect runs

```bash
hiraku launch train --record <record-id> --dry-run
hiraku launch train --record <record-id>
hiraku runs --record <record-id> --json
hiraku runs --record <record-id> --descendants --json
```

The launcher provides `HIRAKU_RECORD_ID`, `HIRAKU_RECORD_FILE`, `HIRAKU_ROOT`,
and `HIRAKU_ENTRYPOINT`. Dispatchers also report `HIRAKU_COMMIT` and
`HIRAKU_GIT_STATE`. A run should record `record_id` so Hiraku can attribute it.
Direct runs are the default; `--descendants` includes the whole subtree.

Use the scope the user requested. Inspect relevant records and code before
editing or launching. Do not invent run results or conclusions. Record the
observed evidence and explain uncertainties. Ask before starting an autonomous
research loop or performing destructive Git operations.
