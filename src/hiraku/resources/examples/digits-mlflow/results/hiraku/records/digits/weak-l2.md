---
id: weak-l2
parent_id: digits
related_to: []
type: hypothesis
status: concluded
entrypoints:
  train:
    command:
    - uv
    - run
    - python
    - train.py
    args:
      alpha: 0.0001
      epochs: 20
      hidden_units: 64
      validation_fraction: 0.25
---
## weak-l2

L2 alpha: 0.0001; seeds: 1, 2, 3; epochs: 20; hidden units: 64; validation fraction: 0.25.
