# Records

## `Record`

::: hiraku.record.Record

`Record` is Hiraku's only research entity. Tasks, experiments, contexts, and
literature share the same model and use `type` to describe their role. A record
stores validated YAML front matter and a Markdown body.
Use `record.metadata` to inspect a serialized snapshot of its metadata and
`record.markdown` to access its body. Update metadata through the record's fields;
changing the returned dictionary does not change the record.
Use `Record.load(path)` to load a record directly from a Markdown file. The
loaded record retains its source path; saving it through a vault requires that
the path is inside that vault and preserves the usual identity checks.

Records receive a UUID when created. Existing readable IDs remain supported.
Their Markdown body and validated front matter are stored together in the
project's records directory, using a filesystem-friendly name such as
`hiraku/records/digits.md`. Names that resolve to the same filename collide.
Records already stored in one-level subdirectories remain discoverable.
Choose a record's parent when creating it; its parent cannot be changed after
the record is saved.

## `Vault`

`Project.vault` provides the project-scoped record collection. The vault reads
files on demand and supports ID-or-name lookup with `get()` and `find()`, as
well as listing, saving, deleting, and collection
validation. Name lookup raises an error when multiple records share a name.

`Vault.save(record)` validates and saves that record without scanning other
records. It checks the existing file and prevents filename collisions, but it
does not check whether IDs are unique across files or references exist. Call
`Vault.validate()` for collection-wide ID, relationship, and cycle checks.

::: hiraku.record.Vault

## `Metric`

::: hiraku.record.Metric
