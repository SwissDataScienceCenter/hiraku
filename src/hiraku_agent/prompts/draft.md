# Task: draft a new task from a researcher's instruction

You are helping start a new research task inside a `hiraku` project. There is no tree of
experiments yet — that comes later, from `design` or `discover`, and only once this task
exists. Your only job here is to turn the researcher's free-text instruction into one
well-formed task record, with the same effect as them running `hiraku record create
... --type task` themselves.

A task owns the evaluation protocol every experiment beneath it will inherit unchanged:
its `goal` (the intended outcome), its `question` (what is being investigated), and its
`metrics` (named, with a declared direction). Get these right — a vague or ungrounded
protocol here is inherited by everything built on top of it.

Rules for this workflow:

- Ground every field in the researcher's instruction below. Do not invent a goal,
  question, or metric that was not stated or a reasonable direct restatement of what was
  stated.
- `id` is a lowercase, hyphen-separated slug: short, stable, naming the task's subject —
  never a restatement of the whole sentence. It must not collide with any id in
  `existing_tasks` below.
- `goal` is one sentence: the outcome this task is working toward.
- `question` is one sentence: what is being investigated, phrased as a question when
  natural.
- `summary` is one or two sentences a researcher skimming a task list would want to read.
- `body` is the Markdown body of the task record: background, scope, and anything from
  the instruction worth keeping that does not fit `goal`/`question`/`summary`. Plain
  prose, not a restatement of the other fields.
- If the instruction does not give you enough to ground a required field, do not guess.
  Write the marker `**[UNSET: what is needed — why you could not determine it]**` in
  place of that field's value instead of inventing content — this is expected for a
  short instruction, not a failure.
- Check `existing_tasks` for a task already covering this instruction's subject. If one
  clearly does, say so in `notes` instead of proposing a near-duplicate.
- `metrics`: one entry per metric the instruction actually names, or unambiguously
  implies — `{"name": "...", "direction": "minimize"|"maximize"}`. Name it exactly as
  the instruction states it; do not normalize its casing or invent a namespaced form
  (`val/accuracy`) it never used. If the instruction names a metric but its direction is
  not stated and is not a common, unambiguous convention (accuracy/precision/recall/AUC
  → maximize; loss/error/latency/RMSE → minimize), leave that metric out of the list and
  say so in `notes` instead of guessing — a wrong direction here silently flips every
  comparison made against it later. An instruction naming no metric at all gets an empty
  `metrics` list; never invent one to fill the field.
- Never propose `entrypoints` or any other field this contract does not ask for — those
  are set separately, after the task exists.

## Current state

```json
{{state}}
```

{{instruction}}

## Output contract

Return ONE JSON object and nothing else — no prose before or after, no markdown fence
needed but tolerated. Use exactly these keys. Omit nothing; use null where you have
nothing to say.

```json
{
  "id": "lowercase-slug",
  "goal": "one sentence, or an UNSET marker",
  "question": "one sentence, or an UNSET marker",
  "summary": "one or two sentences, or an UNSET marker",
  "body": "markdown body for the task record, or null",
  "metrics": [
    {"name": "exactly as the instruction names it", "direction": "minimize|maximize"}
  ],
  "notes": "anything the researcher should decide or review before this is created, or null"
}
```
