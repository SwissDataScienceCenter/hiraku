# Records

## `Record`

::: hiraku.record.Record

`Record` is Hiraku's only research entity. Tasks, experiments, contexts, and
literature share the same model and use `type` to describe their role. A record
stores validated YAML front matter and a Markdown body.
New records require a nonblank `type`. The names `index` and `log` are
reserved for OKF documents and cannot be used as record filenames.
Creating a child record appends a relative Markdown link to its parent. This
link is navigational; the directory structure determines the hierarchy, and
links are not automatically updated when directories are moved.
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

::: hiraku.record.Vault

`Vault` is a filesystem-backed collection that manages records.

## `Metric`

::: hiraku.record.Metric
