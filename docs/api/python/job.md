# Jobs and compute

::: hiraku.job

`JobSpec` is the immutable request passed to a compute target. `Job` is the
persisted receipt returned after submission and by the inspection functions.
The request contains the selected source mode and provenance. Jobs use a managed
local source snapshot. The compute backend receives only the immutable `JobSpec`
and owns execution lifecycle.

`SlurmBackend` is a Submitit adapter selected by `hiraku run --backend slurm`.
The job snapshot and state directory must be visible on Slurm compute nodes,
and the job's Python environment must be available there.

::: hiraku.compute.ComputeBackend

::: hiraku.compute.LocalBackend

::: hiraku.compute.SlurmBackend
