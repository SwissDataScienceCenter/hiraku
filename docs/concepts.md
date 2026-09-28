# Hiraku concepts

Hiraku connects research questions, implementation, and observed runs through
two structures: **records** and **runs**.

A record is a Markdown file with YAML front matter. It can describe an overall
research goal, a proposed experiment, a diagnosis, a review, or a conclusion.
The optional `type` field describes its role; no type is required to create or
run a record. A record may bind an implementation commit, define entrypoints,
and carry metric and tracking settings.

Each record has at most one `parent_id`. The parent defines the organizational
tree and inheritance. A root is stored at
`hiraku/records/{root_id}/{root_id}.md`; every descendant is stored at
`hiraku/records/{root_id}/{record_id}.md`, even when the logical tree has more
than two levels. IDs are unique across the project.

## Inheritance and links

An omitted field inherits from the nearest ancestor that defines it. A local
value wins, and an explicit `null` clears an inherited value. Entrypoints
merge by name: a child may override `train` and still use its parent's
`evaluate` entrypoint. `hiraku record show ID` displays effective values and
their defining records; `--raw` shows the stored file.

`id`, `parent_id`, `related_to`, `type`, `commit`, `status`, and `decision` are
record-local. In particular, a child does not silently claim its parent's
commit or completed decision. `related_to` is a list of record IDs for cross
references. It does not affect inheritance or claim derivation. Explain the
meaning of a link in Markdown when it matters.

## Runs and execution

Runs belong to a tracking backend such as CSV, MLflow, or W&B. Each run
references one `record_id`. `hiraku runs --record ID` returns direct runs;
add `--descendants` to aggregate the subtree. A record's effective tracking
configuration chooses the backend for its runs.

Entrypoints declare commands and default arguments. Launch one with
`hiraku launch NAME --record ID`. The dispatcher receives the record ID and
file path, the project root, and entrypoint name. It reports the actual Git
commit and state. `commit` on a record is a local binding to source code; Git
history and `related_to` serve different purposes.

## Active record

The project configuration stores one `active_record_id`. Creation activates
the new record by default. `hiraku record set ID` changes it, and
`hiraku status --json` shows it. Commands that accept `--record` use the
explicit ID instead.

See [Migrating to records](guides/migrating-to-records.md) for projects using
the earlier task and experiment layout.
