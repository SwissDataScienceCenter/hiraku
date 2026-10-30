# Records

## `Record`

::: hiraku.record.Record

`Record` is Hiraku's only research entity. Tasks, experiments, contexts, and
literature share the same model and use `type` to describe their role. A record
stores validated YAML front matter and a Markdown body.

Use `record.save_markdown(body)` to save a replacement body while preserving
metadata. It returns a saved copy and leaves the supplied object unchanged. The
record must already have a path; changes on disk since loading are rejected.
This check does not lock the file against concurrent writers.

## Registry helpers

::: hiraku.record.load_record

::: hiraku.record.list_records
