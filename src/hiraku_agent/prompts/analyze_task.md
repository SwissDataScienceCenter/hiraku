{{rules}}

---

# Task: analyse a whole task — what do its nodes show, separately and together?

Everything before this judged one node at a time against its own expectation. This call is
the task-level counterpart: you are given the task and every node beneath it, and you are
asked what the evidence says about the task's `question` and `goal` as a whole.

What that means depends on the task, and you should say which kind it is:

- A **comparison or benchmark** (several methods against one protocol): rank them, say
  which are tied, and what each costs.
- A **hypothesis tree**: which hypotheses were supported or refuted, and whether the
  surviving ones fit together into one explanation.
- An **investigation or diagnosis**: what was learned about the cause, even where no
  node set out to win anything.
- Usually a **mixture**. Nodes can be parallel and independent and still say something
  jointly that none says alone — a diagnostic that explains why a method failed, two
  nodes whose results contradict each other, a confound shared by several. That joint
  reading is the part only this call can do.

Rules for this workflow:

- **Know what each piece of evidence is.** Numbers from runs are measurements; a node's
  written `findings`/`conclusions` are what that node's own analysis established, and no
  more. Quote a node's text as that node's finding (cite its id); never turn it into a
  measurement, and never restate a number that is not in `metric_table` or a node's own
  record.
- **Mark inference as inference.** A pattern you see across nodes that no node tested is
  a hypothesis for a person to check, not a result. Say what would test it.
- **Where `metric_table` is present, every number you cite comes from it** (per-node mean,
  spread, run count, seed values, per-metric ranking, the not-dominated set). It was
  computed by the tool from the run rows. Do not recompute or round it differently. It is
  absent when no declared metric has numbers; then do not invent one.
- **Judge with the task's `## Decision rule`** (in the task's `markdown`) wherever it
  applies — its target, guards, report-only metrics, tolerance and how to compare methods
  with each other. State which tolerance you used and where it came from. If the rule
  gives a tolerance by method, compute it from the baseline's `values`. If there is no
  usable tolerance, report differences as not separable rather than inventing one. Treat
  methods within tolerance of each other as tied. Where the rule does not apply (for
  example the nodes are diagnostics, not methods), say so and reason from the evidence
  instead.
- Cost metrics (time, memory) are trade-offs, never the reason one method beats another on
  quality.
- Each node's own `verdict` answers a narrower question — did *it* meet its expectation —
  and is context here. This call does not set or change any verdict.
- If `scope` is present, the researcher chose to analyse only `analysed_only`; the nodes in
  `not_part_of_this_analysis` exist but are not shown to you. Answer about the chosen nodes,
  say so in your conclusions, and do not draw whole-task conclusions.
- A node's `investigation` is what a person or agent read and inferred from files, logs or
  code on that node, in dated entries: treat the cited parts as that node's findings and any
  part labelled as inference as unverified.
- If `pending_nodes` is not empty, or some nodes have neither runs nor findings, the
  picture is partial. Say which are missing and that the answer may change.
- You may NOT decide that a method is adopted or promoted — that is an acceptance
  judgement. You may recommend one for a person to review.

## Current state

```json
{{state}}
```

{{instruction}}

## Output contract

```json
{
  "findings": "markdown: what each node or sensible group of nodes showed — quantitative results (citing metric_table where there is one) and what diagnostic or investigative nodes established — citing node ids",
  "insights": "markdown: what the nodes show TOGETHER that no single node does — patterns, contradictions, confounds, mechanisms, what one result implies for another; label untested inferences as such; null if there is nothing beyond the individual findings",
  "conclusions": "markdown: the answer to the task's question and goal as far as the evidence goes, a recommendation for a person to review where one applies, and what this does NOT establish",
  "next_steps": "markdown: what to run or decide next",
  "missing_evidence": "what would need to be run or checked to settle the question, or null"
}
```
