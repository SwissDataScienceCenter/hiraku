---
id: create
accepted_types: [task, experiment]
effect: create
type_prompts:
  task: task.md
  experiment: experiment.md
hooks: hooks.py
---
Create one research record. Return JSON only; do not edit files or commit.
Hiraku assigns the type, parent and initial lifecycle status. If supplied_id is
present in the state, use that ID; otherwise propose a unique descriptive ID.

{{type_prompt}}

## Current state
```json
{{state}}
```

## Additional instructions
{{instruction}}
