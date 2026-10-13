---
id: heart-disease
type: task
status: active
metrics:
  - name: val/log_loss
    direction: minimize
  - name: val/accuracy
    direction: maximize
---
# Heart disease classification

Can a random forest improve on a logistic regression baseline?

This is a ready-to-browse dashboard fixture. The runs are **synthetic examples**,
not measured results or clinical evidence. No training or network access is needed.

Select an experiment to highlight its runs. Compare `val/log_loss` (lower is
better) and `val/accuracy` (higher is better). `duration` is an undeclared metric,
so it displays dots without a running-best line.
