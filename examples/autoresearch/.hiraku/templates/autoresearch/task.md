---
id: "{{id}}"
summary: "Improve autoresearch validation bits per byte"
goal: "Minimize val_bpb under a fixed five-minute single-GPU training budget."
metrics:
  - name: val_bpb
    direction: minimize
branch: "{{branch}}"
---

# {{title}}

## Goal

Improve the pinned autoresearch baseline by changing only `train.py` while minimizing validation bits per byte (`val_bpb`).

## Scope

- Keep `prepare.py`, the evaluation function, dependencies, and five-minute GPU budget fixed.
- Modify only `train.py` for scientific experiments.
- Use one canonical Hiraku experiment branch and record per coherent idea.
- Treat GPU memory as a soft constraint and prefer simpler changes when results are equivalent.

## Implementation plan

Establish the baseline, iterate through bounded experiments, compare task-scoped runs, and integrate only accepted commits into the task branch using normal Git.

## Evaluation protocol

- Primary metric: `val_bpb`, lower is better.
- Budget: five minutes of training time on one NVIDIA GPU, with a ten-minute hard timeout.
- Secondary evidence: peak VRAM, MFU, training duration, and implementation complexity.
- `cpu-mock` validates orchestration only and is never evidence of model quality.

## Findings

## Open questions

## Conclusions

## Decisions and next steps

## Changelog
