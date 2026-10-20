# Records

## `BaseRecord`

::: hiraku.base.BaseRecord

`BaseRecord` provides generic Markdown/YAML persistence. Programs, actions,
and contexts extend it.

Use `record.save_markdown(body)` to save a replacement body while preserving
metadata. It returns a saved copy of the same subtype and leaves the supplied
object unchanged. The record must already have a path; changes on disk since
loading are rejected. This check does not lock the file against concurrent writers.

## `Record`

::: hiraku.record.Record

`Record` is the research entity. It adds a single parent, optional related
records, inheritance, entrypoints, run queries, and project registry methods.

## Registry helpers

::: hiraku.record.load_record

::: hiraku.record.list_records
