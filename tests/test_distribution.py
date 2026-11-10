from __future__ import annotations

import subprocess
from pathlib import Path
from zipfile import ZipFile

REPOSITORY = Path(__file__).parents[1]
PACKAGE = REPOSITORY / "src" / "hiraku"
EXAMPLES = REPOSITORY / "examples"
SKILLS = PACKAGE / "resources" / "skills"
WORKFLOWS = PACKAGE / "resources" / "workflows"
DASHBOARDS = PACKAGE / "resources" / "dashboards"


def test_wheel_contains_all_packaged_example_files(tmp_path: Path) -> None:
    result = subprocess.run(
        [
            "uv",
            "build",
            "--offline",
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
        f"hiraku/{path.relative_to(PACKAGE).as_posix()}"
        for resource_root in (SKILLS, WORKFLOWS, DASHBOARDS)
        for path in resource_root.rglob("*")
        if path.is_file() and "__pycache__" not in path.parts
    }
    expected.update(
        f"hiraku/resources/examples/{path.relative_to(EXAMPLES).as_posix()}"
        for path in EXAMPLES.rglob("*")
        if path.is_file() and "__pycache__" not in path.parts
    )
    assert any(path.endswith("collaborative/workflow.md") for path in expected)
    expected.add("hiraku/workflows/__init__.py")
    expected.add("hiraku/examples.py")
    expected.add("hiraku/workflows/langgraph.py")
    expected.update(
        {
            "psij-descriptors/local.py",
            "psij-descriptors/renku.py",
            "psij-descriptors/_local_supervisor.py",
            "hiraku/resources/launchers/single.sh",
        }
    )
    with ZipFile(wheels[0]) as wheel:
        packaged = set(wheel.namelist())

    assert expected <= packaged, f"Missing packaged resources: {expected - packaged}"

    dashboard = {
        f"hiraku/dashboard/{name}"
        for name in (
            "components.py",
            "data.py",
            "launcher.py",
            "provider.py",
        )
    }
    assert dashboard <= packaged, f"Missing dashboard files: {dashboard - packaged}"
    assert (
        not {
            "hiraku/workflows/workflow.py",
            "hiraku/workflows/session.py",
            "hiraku/workflows/graph.py",
            "hiraku/workflows/markdown.py",
        }
        & packaged
    )
    assert not any(name.startswith("hiraku/workflow/") for name in packaged)
