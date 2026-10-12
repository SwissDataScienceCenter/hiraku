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

- Two kinds of content go into a task, and they are treated differently:
  - **Stated** content — the goal, the question, what is being compared — comes from the
    researcher's instruction. Ground it there; do not invent a goal or question that was
    not stated or a direct restatement of what was stated.
  - **Protocol** content is only what must be identical for every experiment under this
    task, so that their results can be compared: the metrics and their direction, the
    evaluation data or split (by name), how many seeds or repeats, how an ambiguous phrase
    in the instruction is read, and the decision rule. When the instruction leaves one of
    these open, propose a concrete, conventional choice with a one-line rationale
    (Recall@k / mAP for ranking, AUC / F1 for a binary decision, latency as a secondary
    cost). Do not push it back to the researcher when a standard default exists.
  - Everything else is **not** the task's business: thresholds, hyperparameters,
    preprocessing and resolutions, checkpoints and model variants, GPU or memory budgets,
    file paths. Do not write them here. `design` records them per experiment, where they
    can be looked up in the project's code and corrected one at a time. Leaving them out
    is correct, not incomplete.
  - Where something is to be the same for all methods (input size, preprocessing, a
    ground-truth file), state the **principle**, not a number: "all methods are evaluated
    on identical inputs and the same ground truth". If a method may need an exception,
    say it is allowed only with a control run under the common setting. Do not freeze a
    concrete value such as "resize to 640" into the task: it would block methods that
    need something else and cannot be edited once experiments exist.
- A proposal is not a measurement. Never state or imply that a number came from an
  experiment, and never invent results. A value you chose is an initial value, said so.
- `id` is a lowercase, hyphen-separated slug: short, stable, naming the task's subject —
  never a restatement of the whole sentence. It must not collide with any id in
  `existing_tasks` below.
- `goal` is one sentence: the outcome this task is working toward.
- `question` is one sentence: what is being investigated, phrased as a question when
  natural.
- `summary` is one or two sentences a researcher skimming a task list would want to read.
- `body` is the Markdown body of the task record: background, scope, and anything from
  the instruction worth keeping that does not fit `goal`/`question`/`summary`. Plain
  prose, not a restatement of the other fields. Do NOT put the metric list or the proposed
  defaults here — they have their own keys and the tool writes them into a
  `## Proposed protocol` section for you.
- `metrics`: the task's evaluation metrics — `{"name": "...", "direction":
  "minimize"|"maximize", "role": "primary"|"secondary", "rationale": "..."}`. Include one
  primary metric for every thing the instruction says is being evaluated, plus secondary
  ones (cost, latency, robustness) when they would change a decision. If the instruction
  names a metric, use its name exactly as written. If it names none, propose conventional
  ones using their standard names. A metric always has a direction; choose the
  conventional one (accuracy/precision/recall/AUC/mAP → maximize; loss/error/latency/RMSE
  → minimize).
- `proposed_defaults`: the comparability choices you made on the researcher's behalf —
  `{"item": "...", "value": "...", "status": "found|default|choice|unset", "rationale":
  "..."}`. Only the protocol items listed above: how an ambiguous phrase is interpreted
  (e.g. one combined entry or two separate ones), the evaluation dataset / split, the
  comparison protocol (seeds, repeats), and the "same for all methods" principles. If
  project context names a file, dataset or definition, use it and name the file
  (`found`). No thresholds, hyperparameters, paths or hardware here.
- `decision_rule`: how results will later be turned into verdicts, as a short Markdown
  block (the tool writes it into a `## Decision rule` section). If the researcher
  specified a rule, restate it faithfully. Otherwise propose one with this shape:
  - **Target** — the metric (or metrics read together) that decides whether a method is
    better; usually the one closest to the end goal.
  - **Guards** — metrics that must not get meaningfully worse.
  - **Report only** — cost metrics (time, memory): always listed, never decisive.
  - **Tolerance** — the size of difference that counts. If the instruction gives no
    number, define it by method rather than inventing one: twice the baseline's
    seed-to-seed standard deviation, measured from at least three baseline seeds. Give a
    number only when the instruction does, and call it an initial value.
  - **Verdict against a baseline** — `supports`: target better beyond tolerance and no
    guard worse beyond tolerance. `refutes`: target worse beyond tolerance.
    `inconclusive`: differences within tolerance, a guard worse, or too little evidence
    (for example one seed). When it is hard to tell, report a candidate result and lean
    `inconclusive`.
  - **Comparing methods with each other** — drop methods whose guards got worse, rank
    the rest by the target, treat differences within tolerance as ties, and list cost
    trade-offs separately.
- `candidate_methods`: only for a benchmark or comparison task, i.e. one whose goal is to
  compare several methods, models or settings. Say whether the researcher named them:
  `{"status": "specified", "methods": ["...", ...]}` listing exactly the ones the
  instruction names (a count such as "five methods" with no names is NOT specified), or
  `{"status": "unspecified"}` when the instruction asks to benchmark methods without
  naming them. Never fill in methods yourself here: finding candidates is `discover`'s
  job, and an invented list would be read later as the researcher's own. Use `null` for
  any task that is not a comparison.
- `notes` is for the few things you could not settle and a wrong guess would be costly
  (see the rules on unknown values below). Keep it short; most drafts need no note. A
  protocol item with no grounded value gets the `unset` marker in `proposed_defaults`
  instead of a vague phrase.
- Check `existing_tasks` for a task already covering this instruction's subject. If one
  clearly does, say so in `notes` instead of proposing a near-duplicate.
- Never propose `entrypoints` or any other field this contract does not ask for — those
  are set separately, after the task exists.

{{assumptions}}

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
    {
      "name": "metric name",
      "direction": "minimize|maximize",
      "role": "primary|secondary",
      "rationale": "one line: why this metric, why this direction"
    }
  ],
  "proposed_defaults": [
    {"item": "what was chosen", "value": "the chosen value", "rationale": "one line"}
  ],
  "decision_rule": "markdown: target, guards, report-only, tolerance, verdicts, ranking",
  "candidate_methods": {"status": "specified|unspecified", "methods": ["only if specified"]} or null,
  "notes": "only what the researcher must still decide, or null"
}
```
