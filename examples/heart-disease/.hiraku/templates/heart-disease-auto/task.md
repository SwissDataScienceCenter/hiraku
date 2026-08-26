---
id: "{{id}}"
summary: "Autonomously improve heart-disease risk prediction"
goal: "Minimize validation log loss on the checksum-pinned Cleveland cohort."
metrics:
  - name: val/log_loss
    direction: minimize
  - name: val/accuracy
    direction: maximize
branch: "{{branch}}"
---

# {{title}}

## Goal

Find an in-scope model configuration that minimizes validation log loss while training only on the
fixed training split.

## Fixed evaluation protocol

- Dataset: UCI `processed.cleveland.data`, SHA-256 `a74b7efa387bc9d108d7d0115d831fe9b414b29ae7124f331b622b4efa0427c8`.
- Target: binary presence of heart disease (`num > 0`).
- Split: stratified 75% training and 25% validation, seed 42.
- Primary metric: validation log loss, lower is better.
- Secondary metric: validation accuracy, higher is better.
- The data, checksum, split, preprocessing, metrics, and evaluation code are fixed.

## Autonomous scope

The agent may edit the training implementation and model configurations using existing dependencies
only. It must not add dependencies or change the fixed evaluation protocol. Before starting, record
and obtain approval for either a maximum iteration count or wall-clock limit in `program.md`.

## Findings

## Conclusions

## Decisions and next steps

## Changelog
