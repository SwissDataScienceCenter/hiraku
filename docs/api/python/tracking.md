# Tracking API

::: hiraku.tracking.TrackingBackend

::: hiraku.tracking.CSVBackend

::: hiraku.tracking.MLflowBackend

`MLflowBackend.list_runs(read_only=True)` treats a missing SQLite database as
an empty source and queries existing databases through a temporary backup,
so MLflow initialization or migrations cannot modify the original. The dashboard
uses this mode. HTTP(S) and Databricks tracking servers use normal query APIs;
other direct storage sources are rejected in read-only mode.
