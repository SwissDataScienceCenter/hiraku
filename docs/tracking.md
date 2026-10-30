# Tracking backends

Hiraku uses a small adapter interface to query normalized `Run` objects while
your training code logs through the selected backend's native API.

Choose the backend that matches the stage of your project:

- [CSV](guides/tracking-csv.md) for local and offline work;
- [W&B](guides/tracking-wandb.md) for hosted run tracking and collaboration;
- [MLflow](guides/tracking-mlflow.md) for local SQLite or a remote MLflow server.

Set `tracking_backend` in `hiraku.yml` to choose one backend for the whole
project. Records cannot override it, and it is not stored in record front
matter. Remove any existing `tracking_backend` field from record front matter
and move its value into `hiraku.yml`. If omitted there, Hiraku uses CSV. The
[tracking API reference](api/python/tracking.md) documents the shared backend
contract and normalized run model.

For a custom project, configure the backend once in `hiraku.yml`:

```yaml
tracking_backend: wandb
```

Use a mapping instead of a shorthand when the backend needs options. The
backend guides below show each adapter's supported settings.

Normalized runs accept only the documented fields: `id`, `metrics`, `params`,
`record_id`, `commit`, `timestamp`, `duration`, `entrypoint`, `url`, and `raw`.
Put backend-specific metadata in `raw`. Its mapping, along with `metrics` and
`params`, is copied and made shallowly immutable; nested values retain their
original types and mutability.
