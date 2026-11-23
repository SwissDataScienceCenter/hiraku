# Job

See [Jobs and executors](../../guides/jobs.md) for command-line and Python
examples.

## JobRequest

::: hiraku.job.JobRequest
    options:
      members:
        - command
        - executor
        - record_id
        - executor_url
        - custom_attributes
        - directory
        - launcher
      show_if_no_docstring: true
      show_signature_annotations: true

## Submission

::: hiraku.job.Submission
    options:
      members:
        - id
        - record_id
        - submitted_at
        - observed_at
        - executor
        - executor_url
        - executor_work_directory
        - native_id
        - spec
        - status
        - from_psi_job
        - get_psi_job
        - sync
        - save
        - logs
        - cancel
        - load
        - from_prefix
        - list
      show_if_no_docstring: true
      show_signature_annotations: true

## Functions

::: hiraku.job.get_executor
    options:
      show_root_heading: true
      show_root_full_path: false
      show_signature_annotations: true

::: hiraku.job.prepare_job
    options:
      show_root_heading: true
      show_root_full_path: false
      show_signature_annotations: true

::: hiraku.job.run_local
    options:
      show_root_heading: true
      show_root_full_path: false
      show_signature_annotations: true

::: hiraku.job.observe_submission
    options:
      show_root_heading: true
      show_root_full_path: false
      show_signature_annotations: true
