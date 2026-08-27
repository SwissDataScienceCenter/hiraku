{{rules}}

---

# Task: draft a goal record from a research question

The researcher has written a question in their own words. Turn it into a complete goal record: the
metric, the direction, the success criterion, and above all the **evaluation protocol** that every
experiment under this goal will inherit unchanged.

You are also given the project's own files under `project_context` — its memory file, README,
documentation and notes. Read them before you write anything. Metric definitions, dataset paths,
split conventions and prior decisions are usually already written down there, and a goal drafted
from them is one the researcher can accept rather than rewrite.

## What makes this record usable

The protocol has to be specific enough that two people implementing it independently would compute
the same number. That is the bar. Concretely:

- **A metric is a formula, not a name.** `reprojection_error` is a name. "Median over evaluation
  frames of the pixel L2 distance between the observed keypoint and the reprojection of its
  triangulated 3D point, at native resolution, dropping frames with fewer than N valid points" is a
  metric. State the reduction (mean/median), the unit, and what is excluded — those three choices
  change the number by more than most methods do.
- **Every metric name you use anywhere must be defined in `### Metrics`.** Not only
  `primary_metric` and `gate_metric` — every `lower_snake_case` name you write in the question, the
  success criterion, out of scope or open threads. If your success criterion says a run must not
  raise `pairs_at_cap_fraction`, then `### Metrics` has to say what that is: what is being counted,
  what the cap is, what the denominator is, and why it can be gamed. A criterion that names a
  quantity the record never defines reads as precise and is not.
  If you need such a quantity but cannot define it from what you were given, define it as far as
  you can and put an `**[UNSET: ...]**` inside the definition for the part you cannot supply —
  never drop the name in undefined.
- **A split is a procedure.** Which items, produced how, by which script or seed, and the fact that
  it is not regenerated.
- **A dataset is a version.** A name without a version or checksum cannot be compared against next
  month.
- **Seeds are a count and a list**, and the reduction across them.
- **Held fixed** is the list of things that must not vary between two experiments under this goal,
  because varying them makes the comparison meaningless.

## The UNSET rule — this is the important one

You will not be able to ground everything. Dataset paths, checksums and internal conventions are
often nowhere in the files you were given.

**Never invent one, and never paper over it with a vague sentence.** Where you cannot ground a
required piece of the protocol, write the marker

    **[UNSET: what is needed — why you could not determine it]**

in place of that text, and add a matching entry to the `unset` array. The tool renders those in the
record and refuses to treat the goal as ready until the researcher replaces them. A record with
three honest UNSET markers is far more useful than one with three plausible guesses, because the
guesses are indistinguishable from grounded facts once they are written down.

Prefer a marker over a hedge. "A standard train/test split" is a hedge; it looks filled in and is
not. Use markers for values only — the surrounding sentence should still say precisely what the
value will mean once it is supplied.

## Other rules for this task

- Choose ONE primary metric. If the question implies several, pick the one that decides it and put
  the others in `Open threads`, or name one as `gate_metric` if its role is "must not degrade".
- `target` is a number only if the project files or the question state one. Otherwise null — an
  invented target silently redefines success.
- Scope the question so it is answerable. Move everything else into `out_of_scope` by name, so a
  later loop does not drift into it.
- Cite what you grounded. Every entry in `grounding` names a file from `project_context`. If that
  array is empty, say so in `notes` — the researcher should know the draft is unanchored.
- Do not design experiments here and do not predict results.

## Current state

```json
{{state}}
```

{{instruction}}

## Output contract

```json
{
  "title": "short human title for the goal",
  "primary_metric": "lower_snake_case_name",
  "direction": "minimize|maximize",
  "target": 0.0,
  "gate_metric": "lower_snake_case_name or null",
  "question": "markdown: the falsifiable question, restated precisely",
  "why_it_matters": "markdown: the decision that depends on the answer",
  "success_criterion": "markdown: what result would count as answering it",
  "evaluation_protocol": {
    "dataset": "markdown: dataset, version, checksum",
    "splits": "markdown: which items, produced how, and that they are not regenerated",
    "metrics": "markdown: the exact definition of the primary metric, and of the gate metric",
    "seeds": "markdown: how many, which, and how they are reduced",
    "held_fixed": "markdown: what must not vary between experiments under this goal"
  },
  "out_of_scope": "markdown: named questions this goal does not ask",
  "open_threads": "markdown: known unknowns that are not yet experiments",
  "first_experiments": [
    {
      "id": "lowercase-slug",
      "kind": "baseline|hypothesis|diagnostic|ablation|combination",
      "expectation": "one falsifiable sentence",
      "scope": "markdown: what it tests",
      "rationale": "why it should be first"
    }
  ],
  "unset": [
    {
      "field": "which part of the record carries the marker",
      "why": "what you looked for and did not find",
      "how_to_fill": "what the researcher should write there"
    }
  ],
  "grounding": [
    {"claim": "what you took from the project files", "source": "path/from/project_context"}
  ],
  "notes": "anything the researcher should know before accepting this draft, or null"
}
```
