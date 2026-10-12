from __future__ import annotations

import subprocess
from pathlib import Path
from zipfile import ZipFile

REPOSITORY = Path(__file__).parents[1]
PACKAGE = REPOSITORY / "src" / "hiraku"
EXAMPLES = PACKAGE / "resources" / "examples"
WORKFLOWS = PACKAGE / "resources" / "workflows"


def test_wheel_contains_all_packaged_example_files(tmp_path: Path) -> None:
    result = subprocess.run(
        [
            "uv",
            "build",
            "--wheel",
            "--out-dir",
            str(tmp_path),
        ],
        cwd=REPOSITORY,
        text=True,
        capture_output=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    wheels = list(tmp_path.glob("*.whl"))
    assert len(wheels) == 1

    expected = {
        f"hiraku/{path.relative_to(PACKAGE)}"
        for directory in (EXAMPLES, WORKFLOWS)
        for path in directory.rglob("*")
        if path.is_file() and "__pycache__" not in path.parts
    }
    assert any(path.endswith("autoresearch-python/workflow.md") for path in expected)
    with ZipFile(wheels[0]) as wheel:
        packaged = set(wheel.namelist())

    assert expected <= packaged, f"Missing packaged resources: {expected - packaged}"
