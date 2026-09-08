# Hiraku System Design

Based on what I liked from [*Agentic Design Patterns*](https://doi.org/10.1007/978-3-032-01402-3)

> I have not finished reading every chapter, but there is already enough material to present several interesting points for discussion. (Matteo)

## Design principles

### 1. Define a clear goal

> Any kind of agentic workflow should be bounded by a clear goal definition, and the system should not allow the agent to change it without explicit human approval.

A tool such as Hiraku should define a clear **Goal** with:

- an objective;
- metrics;
- a direction;
- an evaluation protocol;
- constraints;
- entrypoints;
- resource limits; and
- stop conditions.

In auto mode, the system should **stop if the agent proposes changing any of these fields**.

### 2. Model phases and transitions

The system should represent states or model transitions between research phases. Example phases include:

```text
discover → propose → admit → implement → commit → launch
→ analyze → conclude → continue/stop
```

### 3. Record lifecycle events

Events should be linked to states or model transitions. This is highly connected with the core idea in [WikiSkills](https://arxiv.org/abs/2608.27454v1), where the tool could potentially improve its skills later using execution traces and events.

### 4. Distinguish types of evidence

Hiraku should distinguish implementation from execution. Examples include:

- proposal evidence;
- implementation evidence; and
- execution evidence.

### 5. Handle failures explicitly

Failure types and exception handling should allow the system to:

- retry a failed **Run**;
- create a new Experiment after an implementation failure;
- stop after repeated identical failures; and
- preserve the output of failed attempts.

### 6. Evaluate and monitor the agent trajectory

Evaluation and monitoring should cover the agent trajectory, not only the outcome of the evaluation.

### 7. Prioritize proposals using available resources

Suppose a discover agent drafts several directions to solve a problem (**proposals**). How should Hiraku rank them? Possible criteria include:

- expected metric improvement;
- implementation effort;
- run cost; and
- information gain.

> [!TIP]
> A very simple heuristic here will likely be sufficient.

### 8. Use dynamic planning only where necessary

> [!IMPORTANT]
> Fixed workflows are more predictable and less likely to fail. Guided mode should therefore remain deterministic. Launching, logging, and lifecycle transitions should never be LLM-planned.

The dynamic planning steps should be limited to:

- proposal discovery, as a lightweight form of DeepResearch;
- implementation strategy, when implementation is agent-driven; and
- next-Experiment selection.

Only these steps need to be agentic.

### 9. Maintain bounded memory and context

The Memory and Context patterns suggest always keeping a research context with:

- the Goal definition;
- the baseline result;
- the current best result;
- previous hypotheses;
- failed approaches;
- open questions; and
- resource usage.

Before proposing a new Experiment, the agent should receive or have access to this context. This gives it a concise summary of the overall research context.

### 10. Prefer structured agent outputs

In any agent-driven step, **structured outputs** should be preferred and potentially validated to avoid drift. Free-form text can remain available for explanations, such as Markdown files, but lifecycle actions should consume schemas.

## References

- [Agentic Design Patterns](https://doi.org/10.1007/978-3-032-01402-3)
- [WikiSkills: Compiling Agent Experience into Persistent Knowledge for Skill Evolution](https://arxiv.org/abs/2608.27454v1)
