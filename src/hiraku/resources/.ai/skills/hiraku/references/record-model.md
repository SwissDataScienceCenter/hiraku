# Hiraku record model

Hiraku records store research state in Markdown files with YAML front matter.
The project configuration identifies the records directory.

Records form a tree through `parent_id`. A root record defines a research
scope; child records can describe focused work within that scope. Hiraku does
not require every record to have a particular semantic `type`.

Most fields inherit from the nearest ancestor that defines them. An explicit
`null` clears an inherited value. Entrypoints merge by name. Identity and
workflow fields such as `id`, `parent_id`, `related_to`, `type`, `commit`,
`status`, and `decision` are local to each record.

`related_to` creates cross-references only; it does not change the hierarchy or
inherit fields. Record relationships are separate from tracked runs. Runs live
in the configured tracking backend and may carry attribution to a record,
entrypoint, and commit.

Use `hiraku record show <id>` to inspect effective values and their sources;
use `--raw` to inspect the record's own stored fields. Use Hiraku's CLI, Python
API, or MCP operations to persist changes so validation and tree constraints
are applied.

## Git persistence and experiment commits

Research records are authoritative project state, but they are not experiment
implementation files. Whether a project versions `hiraku/records/` in Git is a
project policy; Hiraku does not automatically stage or commit those files.
Keep that policy separate from experiment implementation commits.

An experiment implementation commit should include only its selected source
paths. Do not use `git add .` for that commit. Untracked Hiraku records do not
conflict with a scoped source change and should remain untouched. Unrelated
staged paths must not be included; resolve their staging separately before
committing the experiment.
