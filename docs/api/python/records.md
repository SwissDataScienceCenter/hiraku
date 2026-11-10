# Records

## `Record`

::: hiraku.record.Record

`Record` is document data with a required OKF `id`: its bundle-relative path without the
`.md` suffix. Field validation is local: required nonblank `type`,
metadata shapes, reference syntax, and commit hashes.
Unknown metadata is preserved. `title` is the optional OKF display title.

`status` is restricted to the OKF lifecycle values `draft`, `stable` (the
default), and `deprecated`. `state` is an optional string for workflow-defined
research progress, such as `drafted`, `implemented`, or `analyzed`.

`record.metadata` is a serialized snapshot of front matter. It excludes `id`
and the Markdown body. Loading derives the ID from the filename; it is never
persisted in front matter. Moving a document changes its ID.
`parent_id` and `related_to` are ordinary persisted
relationships to other concept paths. Placement does not determine parentage.

```python
from pathlib import Path
from hiraku.record import Record, Vault

vault = Vault(root=Path("hiraku/records"))
record = Record(id="experiments/baseline", type="experiment", title="Baseline")
vault.save(record)
record = vault.load("experiments/baseline")
record.markdown = "Results from this experiment."
vault.save(record, overwrite=True)
```

## `Vault`

::: hiraku.record.Vault

`Vault` owns bundle operations, validation and filesystem access and persistence.

Saving persists explicitly supplied fields and values changed from their defaults,
including in-place collection edits. Unchanged implicit defaults are omitted.
