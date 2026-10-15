---
id: logistic-baseline
parent_id: heart-disease
related_to: []
type: baseline
status: concluded
entrypoints:
  train:
    command:
    - uv
    - run
    - python
    - train.py
    args:
      model: logistic
      validation_fraction: 0.2
---
## logistic-baseline

Model: `logistic`; validation fraction: 0.2; seeds: 1, 2, 3.

Mean across the three runs:

- `val/log_loss`: 0.42252167
- `val/accuracy`: 0.82513661

No statistical significance is claimed.
