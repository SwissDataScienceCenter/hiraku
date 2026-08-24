---
# required by the CLI — do not remove
id: <experiment-id>
branch: exp/<experiment-id>
mode: guided               # guided | auto; may narrow the parent's, never widen it

# lineage (optional, additive)
goal: <goal-id>            # required on a root; inherited from parent when absent
parent:                    # omit on a root
kind: hypothesis           # baseline | hypothesis | diagnostic | ablation | combination
status: proposed           # proposed | approved | running | concluded | abandoned
verdict:                   # supports | refutes | inconclusive — only when concluded
origin: researcher         # researcher | agent-diagnostic | agent-literature
expectation: ""            # one falsifiable sentence, written BEFORE the first run
created: <YYYY-MM-DD>
concluded:
---

# <Experiment title>

## Scope: hypothesis or task

The bounded research question or implementation task. If this is a child, state which result of the
parent it responds to.

## Expectation and how it could fail

The prose behind `expectation`: the predicted direction and rough size of the effect, on which
metric, by what mechanism.

Then — what result would refute it. A node that cannot state this is not an experiment. Write both
before launching; an expectation recorded after seeing results cannot be triaged.

## Implementation plan

The changes required to execute the experiment.

## Evaluation protocol

Inherited from the goal. State it here only to note something specific to this node that does not
change the comparison. Any actual change to the protocol means this belongs under a different goal.

## Findings

Observed results, with the run IDs they come from. Metrics are read from the run table, never
restated from process output. Record execution defects and which runs they invalidate.

## Conclusions

What the evidence supports and what remains uncertain. For a concluded node this must carry: the
verdict and its failure class, the run IDs supporting it, which investigation-test condition failed
if a child was considered and rejected, and what would change the answer.

## Decisions and next steps

The resulting decision, and any child nodes or proposals.

## Children

Generated. One line per child: ID, kind, status, verdict.
