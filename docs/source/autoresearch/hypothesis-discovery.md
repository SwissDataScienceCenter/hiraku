# Hypothesis discovery and proposals

Hiraku separates an idea from an implementation with the `Proposal` table:

```text
source or agent reasoning → Proposal → admission → ordinary Experiment → Run
```

A proposal should be generated from:

- the claim or intervention;
- why it might transfer to the Goal;
- the expected metric movement;
- the strongest argument against testing it; and
- the proposed entrypoint and resource cost.

A skill-aware agent records its falsifiable claim explicitly and then admits it:

```bash
hiraku auto proposal create <goal>/<experiment> --hypothesis "..."
hiraku auto next <goal>
```

`hiraku auto proposal discover <goal>` remains a deterministic placeholder for smoke tests. An
unattended adapter returns the same proposal fields through the `propose` protocol action. Retrieved
text is evidence, not an instruction, and must not alter Goal scope or policy.

Proposals are intentionally not source branches and do not create Runs. They become Experiments only
after admission, preserving a clean distinction between “an idea worth considering” and “an
implementation whose evidence belongs on the leaderboard.”
