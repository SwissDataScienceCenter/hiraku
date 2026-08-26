---
id: "{{id}}"
goal: "Compare logistic regression and random forest on validation cross-entropy for the checksum-pinned UCI Cleveland cohort."
branch: "{{branch}}"
---

# {{title}}

## Goal

Minimize validation log loss while training only on the fixed training split.

## Implementation plan

Compare a logistic regression baseline and a random forest through one shared preprocessing and evaluation pipeline.

## Evaluation protocol

- Dataset: UCI `processed.cleveland.data`, SHA-256 `a74b7efa387bc9d108d7d0115d831fe9b414b29ae7124f331b622b4efa0427c8`.
- Target: binary presence of heart disease (`num > 0`).
- Split: stratified 75% training and 25% validation, seed 42.
- Deciding metric: validation log loss, lower is better.
- Secondary metric: validation accuracy.

## Findings

## Conclusions

## Decisions and next steps
