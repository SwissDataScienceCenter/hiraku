---
id: scheduler
description: Work through records by priority until each count reaches a target.
---
# Scheduler

## Work

Increase the `count` of record {{record}} from {{count}} to {{next}}, with
`hiraku record update {{record}} --set count={{next}}` or the `record_update`
tool. Change nothing else. Then send the event `incremented` with the record
ID {{record}}.

## Done

Show the user the records in the data with their priority and count.
