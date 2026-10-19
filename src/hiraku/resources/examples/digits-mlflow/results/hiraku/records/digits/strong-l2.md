---
id: strong-l2
parent_id: digits
related_to: []
type: hypothesis
status: concluded
command:
- uv
- run
- python
- train.py
- --alpha
- '1.0'
- --epochs
- '20'
- --hidden-units
- '64'
- --validation-fraction
- '0.25'
---
## strong-l2

L2 alpha: 1.0; seeds: 1, 2, 3; epochs: 20; hidden units: 64; validation fraction: 0.25.
