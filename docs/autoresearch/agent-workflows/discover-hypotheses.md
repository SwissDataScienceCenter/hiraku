# Discover hypotheses

Keep literature or agent reasoning in a Proposal until a human or approved auto policy admits it:

```bash
hiraku auto context <goal>
hiraku auto proposal create <goal>/<experiment> --hypothesis "<falsifiable claim>"
hiraku auto proposal list <goal>
hiraku auto next <goal>
```

A proposal record contains the intended Experiment ID, falsifiable claim, and optional parent.
Generate it using the Goal context, transfer rationale, expected metric movement, counterargument,
and a bounded implementation plan. Admission creates an ordinary Experiment and never bypasses Goal
constraints.
