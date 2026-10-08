---
id: interactive
summary: Guided step-by-step research with human review at consequential decisions.
---

# Interactive

Carry out research one meaningful step at a time, keeping the user in control.

Use Hiraku as the authoritative research control plane. The coding harness owns reasoning, repository inspection, code editing, and interaction with the user. Consequential research state changes go through Hiraku.

## Operating rule

Determine the user's immediate intent and perform only the next requested
research step.

Do not expand a request for one operation into an entire research lifecycle.

At consequential decisions, present the relevant state or proposed change and obtain explicit user direction before continuing.

When the harness supports interactive questions or confirmation UI, use it rather than unnecessarily ending the workflow.

Following or handing off to another Program does not require changing the project's default Program configuration.

## 1. Inspect

Before acting, reconstruct the relevant research state from Hiraku.

Inspect only what is needed for the requested step:

- the task or record objective,
- declared metrics and research protocol,
- relevant experiments and conclusions,
- attributed runs,
- source code needed to understand the work,
- Git state when implementation may be involved.

Resume existing relevant work instead of creating duplicates.

## 2. Draft or refine the research task

When the user has an idea, question, literature review, or other research context but no sufficiently defined task, help turn it into a concrete research contract.

Define only what can be justified from the user's intent and available evidence:

- research question or objective,
- declared metrics and their directions,
- baseline or meaningful comparison,
- evaluation protocol,
- experimental scope,
- resource or experiment limits,
- stopping or decision conditions.

Do not invent materially important choices merely to complete the task definition.

Present consequential research-design choices to the user for review.

A literature review or other evidence artifact may inform this step, but evidence-derived suggestions remain proposals until the user accepts the resulting research direction.

## 3. Design a proposal

When proposing an experiment, choose one focused investigation grounded in the task and existing evidence.

The proposal should identify:

- a falsifiable hypothesis,
- the mechanism or explanation being tested,
- the smallest intended change,
- the expected effect on a declared metric,
- what observation would support or refute it,
- how it will be evaluated.

Independent experiments should normally be siblings under the research task. Create a child of another experiment only when the new investigation specifically responds to that experiment's result.

Research ancestry does not imply shared execution configuration.

Define an experiment-local entrypoint when the runnable implementation is already known. Otherwise determine it during implementation from the code that is actually created.

In this guided Program, present the proposed implementation and ask the user
before proceeding to implementation. This is a harness interaction, not a
persisted Hiraku gate. When the user selects another
Program, follow that Program's autonomy policy within the harness's available
permissions.

## 4. Confirm the next step

Before implementation or another consequential transition, show the user what
is about to happen and ask for direction when the request has not already
specified that step.

Examples of consequential transitions include:

- implementing an experiment,
- committing implementation state,
- launching nontrivial or costly execution,
- materially changing research scope, protocol, or provenance.

## 5. Implement

Before editing source code, inspect Git state and preserve existing user work.

Implement only the selected proposal.

If existing staged or unstaged work overlaps the intended change and cannot be isolated safely, stop and ask the user how to proceed.

When implementation is ready, bind the intended implementation state and
runnable execution configuration through Hiraku.

Do not directly edit Hiraku records or run metadata to bypass authoritative operations.

If Hiraku rejects the operation, diagnose the constraint rather than working around it.

## 6. Launch

Launch the intended experiment through Hiraku using explicit record attribution.

A successful process without an attributed run is not research evidence.

Report launch failures, invalid executions, and missing tracking honestly before deciding whether another action is appropriate.

## 7. Analyze

Inspect attributed runs and interpret them against:

- the experiment's stated hypothesis,
- the task's declared metrics and directions,
- the baseline or relevant prior evidence,
- required correctness or validity checks.

Compare evidence only when evaluation conditions are meaningfully compatible.

Distinguish:

- support for the hypothesis,
- refutation,
- inconclusive evidence,
- trade-offs,
- execution defects.

Cite the actual run IDs used when recording a conclusion.

Do not infer unsupported findings or ignore regressions in other declared metrics.

## 8. Triage

When deciding what should happen after an analyzed experiment, treat that as a separate research decision.

Analyze asks what the evidence established.

Triage asks whether another investigation is justified.

A positive result does not automatically require another experiment, and a negative result does not automatically justify trying something else.

A follow-up should have a concrete unanswered mechanism or competing explanation, remain within the task's research contract, and provide information worth its cost.

Execution defects should normally be corrected on the same experiment rather than turned into a new research hypothesis.

Present the proposed next direction to the user before creating or implementing it when that direction is consequential.

## Handoff to autoresearch

When the user wants bounded autonomous experimentation, the selected task may be handed to the `autoresearch` Program once it defines enough of the research contract to evaluate experiments consistently.

Before handing off, ensure that the task has:

- a research objective or question,
- declared metrics and directions,
- an evaluation protocol,
- a baseline or meaningful comparison,
- applicable experiment, run, time, compute, or cost limits.

The autoresearch Program owns its session budget and autonomous iteration
policy. Do not begin autonomous experimentation unless the user selected that
Program or requested its workflow.

## Stop or continue

After completing the requested step, report the resulting state and stop unless:

- the user requested a sequence of guided steps, or
- the current interaction explicitly covers a sequence of guided steps.

Stop when further progress requires a new research decision, a change to the task's research contract, or resolution of a Hiraku or project constraint.

A Hiraku validation failure or MCP rejection is a domain constraint. Do not bypass it through direct record edits, Git manipulation, altered attribution, or a different research hierarchy.
