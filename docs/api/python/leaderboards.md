# Leaderboard

[View source on GitHub](https://github.com/SwissDataScienceCenter/hiraku/blob/main/src/hiraku/tracking/leaderboard.py) · [Run source](https://github.com/SwissDataScienceCenter/hiraku/blob/main/src/hiraku/tracking/base.py)

See [Leaderboards](../../guides/leaderboards.md) for configuration and CLI examples.

## Leaderboard

::: hiraku.tracking.Leaderboard
    options:
      members:
        - source
        - metrics
        - create
        - load
        - list
        - delete
      show_if_no_docstring: true
      show_signature_annotations: true

## Metric

::: hiraku.tracking.Metric
    options:
      members:
        - name
        - direction
      show_if_no_docstring: true
      show_signature_annotations: true

## Run

::: hiraku.tracking.Run
    options:
      members:
        - id
        - job_id
        - metrics
        - params
        - record_id
        - commit
        - timestamp
        - duration
        - url
        - raw
      show_if_no_docstring: true
      show_signature_annotations: true
