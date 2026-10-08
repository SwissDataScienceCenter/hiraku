from hiraku import BaseRecord, Entrypoint, HirakuConfig, Metric, Record, Run
from hiraku.core.errors import GitError, HirakuError
from hiraku.core.io import atomic_write, dump_yaml, load_yaml_mapping
from hiraku.core.launch import launch, render_arguments
from hiraku.core.record import list_records, load_record
from hiraku.tracking import TrackingBackend


def test_documented_imports() -> None:
    assert all(
        value is not None
        for value in (
            Entrypoint,
            BaseRecord,
            GitError,
            HirakuConfig,
            HirakuError,
            Metric,
            Record,
            Run,
            TrackingBackend,
            launch,
            list_records,
            load_record,
            atomic_write,
            dump_yaml,
            load_yaml_mapping,
            render_arguments,
        )
    )
