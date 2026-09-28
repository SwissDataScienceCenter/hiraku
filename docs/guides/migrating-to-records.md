# Migrating to research records

The unified record model replaces task and experiment files, Python classes,
CLI commands, and run attribution. Existing project files are not converted
automatically. Back up the project before making these changes.

For each old task at `hiraku/tasks/{task_id}/task.md`, create a root record at
`hiraku/records/{task_id}/{task_id}.md`. Keep its Markdown body and metadata,
remove any obsolete task-specific keys, and add `type: task` if useful.

For each experiment under the task, create a record in the same root folder,
with `parent_id: {task_id}`. Preserve its `commit` and other local fields.
Remove `task_id` from its front matter. Record IDs must now be unique across
the project; rename duplicate experiment IDs and update references.

Replace `active_task_id` and `active_experiment_id` in `hiraku.yml` with one
`active_record_id`. Replace run attribution columns or tags `task_id` and
`experiment_id` with `record_id`: use the child record ID when an experiment
ID was present, otherwise the root ID. Update logging scripts to read
`HIRAKU_RECORD_ID` and `HIRAKU_RECORD_FILE` instead of the task and experiment
environment variables. Historical runs require an explicit data migration if
they should appear in new record queries.

Use `hiraku record ...` in place of task and experiment commands. Use
`hiraku launch ENTRYPOINT --record ID` and `hiraku runs --record ID`.
`--descendants` includes runs across a record's subtree. Generic Python code
that used `hiraku.records.Record` for Markdown persistence should import
`BaseRecord`; research code should import `Record`.
