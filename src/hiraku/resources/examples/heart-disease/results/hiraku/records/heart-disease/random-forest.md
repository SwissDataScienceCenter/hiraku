---
id: random-forest
parent_id: heart-disease
related_to: []
type: hypothesis
status: concluded
command:
- uv
- run
- python
- train.py
- --model
- random_forest
- --validation-fraction
- '0.2'
---
## random-forest

Model: `random_forest`; validation fraction: 0.2; seeds: 1, 2, 3.

Mean across the three runs:

- `val/log_loss`: 0.42219747
- `val/accuracy`: 0.79781421

No statistical significance is claimed.
