---
id: literature-review
summary: Structured literature review with evidence synthesis, gaps, and research directions.
---

# Literature Review

Conduct a structured review of the literature around a research question or topic.

The review may be performed before a Hiraku task exists or may contribute evidence to an existing task.

Use available scholarly research capabilities. Prefer AlphaXiv when it is available and appropriate. If a required scholarly source is unavailable because a provider is not configured or authenticated, report that limitation rather than silently treating the search as complete.

## 1. Define scope

Establish from the user's request, existing research context, or selected Hiraku task:

- the research question or topic,
- relevant population, methods, or problem setting,
- inclusion and exclusion criteria,
- publication date range when relevant,
- the kinds of evidence needed to answer the question.

Infer routine details when safe. Ask the user only when a missing choice would materially change the review.

## 2. Discover evidence

Search for relevant primary literature using available scholarly research tools.

Use multiple search framings when needed to cover:

- important terminology and synonyms,
- competing methods,
- established baselines,
- relevant benchmarks or datasets,
- contradictory findings,
- known limitations.

Do not treat one search result set as exhaustive.

Prefer primary empirical sources for claims about experimental effects. Use reviews or secondary sources to orient the search, not as substitutes for primary evidence when primary evidence is available.

## 3. Select sources

Choose sources that materially contribute to the research question.

For each important source, establish why it is relevant to the review.

Distinguish publication or peer-review status when that distinction matters. Do not infer peer review merely from presence in a preprint index.

## 4. Inspect evidence

Read the parts of each selected source needed to establish:

- method or intervention,
- datasets and experimental setting,
- evaluation protocol,
- measurements and metrics,
- relevant results,
- stated limitations.

When AlphaXiv is available, batch related questions about the same paper when retrieving page-level evidence.

Inspect full paper content or associated code only when needed to resolve an important question or verify an implementation claim.

Preserve enough source identity and location information to trace important claims back to their evidence.

## 5. Synthesize

Synthesize evidence across sources rather than producing a sequence of paper summaries.

Identify:

- findings supported across multiple studies,
- conflicting results,
- methodological differences that may explain disagreement,
- important limitations and threats to generalization,
- unresolved evidence gaps,
- open research questions.

Distinguish what the literature establishes from plausible interpretations or hypotheses.

## 6. Derive research directions

When the review is intended to inform further research, derive a small number of candidate directions grounded in the evidence.

For each candidate direction, identify:

- the unresolved question,
- the mechanism or explanation worth testing,
- a falsifiable hypothesis when supported,
- relevant metrics or evaluation considerations suggested by the literature,
- why the question remains unresolved.

Do not create experiment records or begin implementation as part of this Program.

## Output

Produce a review containing:

- scope and search framing,
- evidence synthesis,
- disagreements and limitations,
- gaps and open questions,
- candidate research questions or hypotheses when relevant,
- citations and provenance sufficient to verify important claims,
- limitations of the review's own coverage.

If the review contributes to an explicitly requested downstream research
workflow for a Hiraku task, persist it as a task-linked Context with
`save_literature_review` before handing off. Record each source's verification
level accurately; discovery summaries are not full-text evidence. For a
standalone review, persist it only when the user requests that.

## Handoff

When no downstream Program is specified, the review may be handed to the
`interactive` Program to turn selected evidence and candidate directions into a
concrete research task.

If the user explicitly requested a sequence that continues with another named
Program after the review, save the review context for the selected task,
deliver the review, and then hand off to that Program.
In that case the review is a phase in the requested workflow, not the end of the
workflow. Do not create experiment records or begin implementation as part of
the review itself.

The next Program is responsible for any work beyond the review. For example,
the `interactive` Program defines and obtains agreement on:

- the research question,
- metrics,
- baseline,
- evaluation protocol,
- experimental scope,
- resource limits,
- stopping conditions.

The review records evidence and candidate directions. It does not grant
operating-system or harness permissions; those remain controlled by the user
and coding harness.

## Stop

By default, stop after delivering the evidence synthesis and candidate research
directions. Continue only when the user explicitly requested a named next phase
or Program, as described under Handoff.

Do not begin implementation as part of the review phase. If the user explicitly
requested a downstream Program, hand off to it after delivering the review.

If the available evidence is insufficient to support a useful research direction, report that as the result rather than inventing one.
