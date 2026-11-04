from hiraku import Metric, Project, Record, Run
from hiraku.errors import GitError, HirakuError
from hiraku.execution import execute_command
from hiraku.io import atomic_write, dump_yaml, load_yaml_mapping
from hiraku.record import list_records, load_record
from hiraku.tracking import TrackingBackend


def test_documented_imports() -> None:
    assert all(
        value is not None
        for value in (
            GitError,
            Project,
            HirakuError,
            Metric,
            Run,
            Record,
            TrackingBackend,
            execute_command,
            list_records,
            load_record,
            atomic_write,
            dump_yaml,
            load_yaml_mapping,
        )
    )
