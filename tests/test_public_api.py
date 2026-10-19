from hiraku import BaseRecord, HirakuConfig, Job, JobSpec, Metric, Record, Run
from hiraku.errors import GitError, HirakuError
from hiraku.io import atomic_write, dump_yaml, load_yaml_mapping
from hiraku.job import submit_job, wait_job
from hiraku.record import list_records, load_record
from hiraku.tracking import TrackingBackend


def test_documented_imports() -> None:
    assert all(
        value is not None
        for value in (
            Job,
            JobSpec,
            BaseRecord,
            GitError,
            HirakuConfig,
            HirakuError,
            Metric,
            Record,
            Run,
            TrackingBackend,
            submit_job,
            list_records,
            load_record,
            atomic_write,
            dump_yaml,
            load_yaml_mapping,
            wait_job,
        )
    )
