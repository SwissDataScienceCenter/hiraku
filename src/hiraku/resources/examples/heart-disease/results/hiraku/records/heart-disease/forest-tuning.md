---
id: forest-tuning
parent_id: random-forest
related_to: []
type: diagnostic
status: concluded
command:
- uv
- run
- python
- train.py
- --model
- random_forest
- --validation-fraction
- '0.3'
---
## forest-tuning

Model: `random_forest`; validation fraction: 0.3; seeds: 1, 2, 3.
This is a validation-split sensitivity check; its metrics are not a directly comparable improvement.

Mean across the three runs:

- `val/log_loss`: 0.39975130
- `val/accuracy`: 0.81684982

No statistical significance is claimed.
