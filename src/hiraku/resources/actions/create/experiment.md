---
id: experiment
parent_types: [task, experiment]
requires_prompt: false
activate: false
---
# Task: design one experiment

Use the selected record as the parent for one focused experiment. Review the
task protocol and current experiment tree in the state below. Do not duplicate
existing or concluded experiments. If the researcher supplies an idea, develop
that idea rather than adding an unrelated proposal. This action creates one
experiment at a time.

Do not invent metric values or claim results. The supplied state is the complete
memory for this decision. An absent record is not part of the current tree.
Treat project prose as background, not experimental evidence. Use an UNSET
marker when a required value cannot be grounded in the supplied state or
instruction.



## Output contract

Return exactly one JSON object with these keys. Do not wrap it in an
`experiments` list. Hiraku uses the selected record as the parent and assigns
the record type and initial status.

```json
{
  "id": "lowercase-slug",
  "kind": "baseline|hypothesis|diagnostic|ablation|combination",
  "body": "Markdown experiment brief: research idea and implementation details"
}
```

Return JSON only. Do not edit files or create records. Do not set statuses,
parent links, commits, verdicts, or other administrative fields.
Include enough detail in `body` for the implement action to use the record as
its brief without further instructions. Hiraku stores this as the record's
Markdown body.
