# Tracking backends

Hiraku uses a small adapter interface to query normalized `Run` objects while
your training code logs through the selected backend's native API.

Choose the backend that matches the stage of your project:

- [CSV](guides/tracking-csv.md) for local and offline work;
- [W&B](guides/tracking-wandb.md) for hosted run tracking and collaboration;
- [MLflow](guides/tracking-mlflow.md) for local SQLite or a remote MLflow server.
- [Custom](guides/tracking-custom.md) for a backend of your choice.

Set `tracking_backend` on a root record as the default for new child records.
When a child is created, the effective setting is copied into its front matter
unless the child explicitly sets its own; later changes to the parent do not
rewrite existing children. The project config does not define tracking sources;
move any former project-level setting to the root record. Records without an
explicit setting use CSV. The
[tracking API reference](api/python/tracking.md) documents the shared backend
contract and normalized run model.
