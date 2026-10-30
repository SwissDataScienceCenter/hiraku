# Tracking API

The project configuration selects one built-in CSV, MLflow, or W&B adapter for
all records. `create_backend` validates that configuration and returns the
adapter.

## Backend factory

::: hiraku.tracking.create_backend

## Shared query interface

::: hiraku.tracking.TrackingBackend

::: hiraku.tracking.CSVBackend

::: hiraku.tracking.MLflowBackend
