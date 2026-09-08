# Use the Python API and notebook

The Python API exposes the same validated objects used by the CLI:

```python
from hiraku import Experiment, HirakuConfig, Metric, Task

config = HirakuConfig.discover()
task = Task.create(
    "iris",
    config=config,
    goal="Maximize Iris accuracy",
    metrics=[Metric(name="test/accuracy", direction="maximize")],
)
experiment = Experiment.create(
    "logistic",
    task=task,
    config=config,
    hypothesis="A regularized linear model is a strong baseline.",
)
```

This creates a proposal: no commit is assigned yet. Re-running the creation
cell with the same IDs raises an already-exists error; use
`load_task` and `load_experiment` from `hiraku.data` to reopen saved records.

The Iris notebook in `examples/iris-wandb/interactive.ipynb`
shows a complete interactive training and tracking workflow.

Browse the [generated Python API](../api/index.md) for model fields and
methods.
