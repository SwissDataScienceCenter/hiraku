# Jobs and compute

::: hiraku.job

`JobSpec` is the immutable request passed to local execution. `Job` is the
persisted receipt returned after submission and by the inspection functions.
The request contains the selected source mode and provenance. Jobs use a managed
local source snapshot. Compute target selection is not part of the Python API
or persisted metadata while local execution is the only available target.

::: hiraku.compute.ComputeBackend

::: hiraku.compute.LocalBackend
