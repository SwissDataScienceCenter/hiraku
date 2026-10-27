---
id: scheduler
engine: langgraph
description: Work through records by priority until each count reaches a target.
arguments:
  records: 3
  target: 2
---
# Scheduler

## Work

Increase the `count` of record {{record}} from {{count}} to {{next}}, with
`hiraku record update {{record}} --set count={{next}}` or the `record_update`
tool. Change nothing else. Then call `workflow_resume` with run ID
`{{run_id}}` and response `{"record_id": "{{record}}"}`. If the update did not
work, report that and do not claim success.

## Done

Show the user this final record summary:

```json
{{records}}
```
