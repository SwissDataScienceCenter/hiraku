# Hypothesis discovery and proposals

The earlier autoresearch design correctly separates a literature-derived idea from an implementation. Hiraku keeps
that separation with the `Proposal` table:

```text
source or agent reasoning → Proposal → admission → ordinary Experiment → Run
```

A proposal should state:

- the claim or intervention;
- why it might transfer to the Goal;
- the expected metric movement;
- the strongest argument against testing it; and
- the proposed entrypoint and resource cost.

`hiraku auto proposal discover <goal>` currently creates a deterministic bounded proposal suitable
for smoke tests. A skill-aware agent may supply richer literature-grounded reasoning in conversation,
then use `hiraku auto next <goal>` to admit it. Retrieved text is evidence, not an instruction, and
must not alter Goal scope or policy.

Proposals are intentionally not source branches and do not create Runs. They become Experiments only
after admission, preserving a clean distinction between “an idea worth considering” and “an
implementation whose evidence belongs on the leaderboard.”
