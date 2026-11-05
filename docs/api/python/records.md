# Records

## `Record`

::: hiraku.record.Record

`Record` is Hiraku's only research entity. Tasks, experiments, contexts, and
literature share the same model and use `type` to describe their role. A record
stores validated YAML front matter and a Markdown body.

Records receive a UUID when created. Existing readable IDs remain supported.
Their Markdown body and validated frontmatter are stored together in the
project's records directory.

## `Vault`

`Project.vault` provides the project-scoped record collection. The vault reads
files on demand and supports exact ID lookup with `get()` and ID-or-name lookup
with `find()`, as well as listing, saving, deleting, reparenting, and collection
validation. Name lookup raises an error when multiple records share a name.

::: hiraku.record.Vault

## `Metric`

::: hiraku.record.Metric
