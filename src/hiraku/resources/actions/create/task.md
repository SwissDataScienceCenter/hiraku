---
id: task
parent_types: []
requires_prompt: true
activate: true
---
# Task: create a new task from a researcher's instruction

You are helping start a research task inside a Hiraku project. There is no
experiment tree yet; that is designed after the task exists. Turn the
researcher's instruction into one well-formed task record.

A task defines the evaluation protocol that experiments beneath it inherit:
its `goal` (intended outcome), `question` (what is investigated), and `metrics`
(names and directions). Ground every field in the instruction. Do not invent a
goal, question, or metric that was not stated or is not a direct restatement.

- `id` is a short, stable, lowercase hyphenated slug naming the subject. It
  must not collide with `existing_tasks`.
- `goal` is one sentence describing the outcome.
- `question` is one sentence, phrased as a question where natural.
- `summary` is one or two useful sentences for someone scanning a task list.
- `body` contains background, scope, and remaining instruction details not
  already captured by the other fields. Use prose, not repetition.
- If required information is missing, use `**[UNSET: what is needed — why it
  could not be determined]**` rather than guessing. This is expected for short
  instructions.
- Check `existing_tasks` for a task covering the same subject. If one does,
  explain the likely duplicate in `notes`.
- Include only metrics the instruction names or unambiguously implies. Preserve
  the metric name as written. Use `maximize` for unambiguous metrics such as
  accuracy, precision, recall, and AUC; use `minimize` for loss, error, latency,
  and RMSE. If the direction is unclear, omit that metric and explain why in
  `notes`. If no metric is named or implied, return an empty list.
- Do not propose entrypoints or fields outside this contract.



## Output contract

Return one JSON object and nothing else. Use exactly these keys. Omit nothing;
use `null` when there is nothing to say.

```json
{
  "id": "lowercase-slug",
  "goal": "one sentence or an UNSET marker",
  "question": "one sentence or an UNSET marker",
  "summary": "one or two sentences",
  "body": "Markdown background and scope, or null",
  "metrics": [
    {"name": "metric name", "direction": "minimize|maximize"}
  ],
  "notes": "anything the researcher should review, or null"
}
```

Return JSON only. Do not edit files or create records. Hiraku creates and
activates the root task.
