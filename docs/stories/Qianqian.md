# Qianqian: Governed, evidence-driven parallel research

The main goal of the workflow is to turn a research question into an auditable tree of experiments without surrendering scientific control to the agent. The evaluation protocol is fixed up front; agents may discover and design proposals, but only a person can approve them for execution. Approved experiments run reproducibly and in parallel, results are explicitly triaged, and promotion to the leaderboard requires protocol compliance, reproduction, and sign-off. The final report is assembled only from recorded evidence. 

## Workflow

```bash
# 1. Configure the research bounds and draft a fixed evaluation protocol.
$EDITOR research/config.md
hiraku-auto context
hiraku-auto goal draft dinov3-crossdomain \
  --question "Would DINOv3 improve reprojection between Mapillary and Natix frames?"
hiraku-auto goal show dinov3-crossdomain
$EDITOR research/goals/dinov3-crossdomain.md  # Resolve every [UNSET: ...].
hiraku-auto check

# 2. Optionally discover hypotheses or design an experiment tree.
# Agent output remains a proposal and cannot be launched.
hiraku-auto agent discover dinov3-crossdomain
hiraku-auto agent design dinov3-crossdomain
hiraku-auto proposal list

# 3. Accept or decline each proposal. Keep declined ideas and their reasons.
hiraku-auto proposal accept feature-whitening
hiraku-auto proposal decline dinov3-layer-choice \
  --reason "Not worth four runs until the backbone swap shows a direction."
hiraku-auto tree

# 4. Run approved, commit-backed experiments in parallel.
hiraku-auto loop dinov3-crossdomain \
  --launch reconstruct \
  --max-parallel 4 \
  --gpus 0,1,2,3 \
  --seed 1
hiraku-auto loop dinov3-crossdomain \
  --launch reconstruct \
  --max-parallel 4 \
  --gpus 0,1,2,3 \
  --seed 2

# 5. Triage the evidence and review any proposed follow-up.
hiraku-auto agent triage dinov3-swap
$EDITOR experiments/dinov3-swap/experiment.md
hiraku-auto proposal list
hiraku-auto tree

# 6. Promote only results that pass every evidence gate and human sign-off.
hiraku-auto promote dinov3-swap \
  --run <candidate-run-id> \
  --signed-off-by "Qianqian Zou"

# 7. Build an evidence-only report and inspect the complete research tree.
hiraku-auto report dinov3-crossdomain
hiraku-auto dash -o tree.html
hiraku-auto ui
hiraku-auto check
```

## Preferred user interfaces

The command-line interface is the primary surface for defining goals, reviewing proposals, launching parallel work, triaging evidence, and promoting results. Research records remain readable and directly editable Markdown files. A generated tree dashboard and interactive UI provide complementary, clickable views for inspecting experiment lineage, runs, findings, and gaps in the record.
