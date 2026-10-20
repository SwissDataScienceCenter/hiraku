from __future__ import annotations

import subprocess
from pathlib import Path
from zipfile import ZipFile

REPOSITORY = Path(__file__).parents[1]
PACKAGE = REPOSITORY / "src" / "hiraku"
EXAMPLES = PACKAGE / "resources" / "examples"
SKILLS = PACKAGE / "resources" / "skills"
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
        for resource_root in (EXAMPLES, SKILLS, WORKFLOWS)
        for path in resource_root.rglob("*")
        if path.is_file() and "__pycache__" not in path.parts
    }
    assert any(path.endswith("collaborative/workflow.md") for path in expected)
    expected.update(
        f"hiraku/workflows/{module}.py"
        for module in ("__init__", "workflow", "session", "graph", "markdown")
    )
    expected.add("hiraku/agent.py")
    with ZipFile(wheels[0]) as wheel:
        packaged = set(wheel.namelist())
        entrypoints = next(
            name for name in packaged if name.endswith(".dist-info/entry_points.txt")
        )
        assert "hiraku.agent:main" in wheel.read(entrypoints).decode()

    assert expected <= packaged, f"Missing packaged resources: {expected - packaged}"

    dashboard = {
        f"hiraku/dashboard/{name}"
        for name in (
            "app.py",
            "branding.py",
            "data.py",
            "launcher.py",
            "theme.css",
            "assets/sdsc-logo.svg",
            "assets/sdsc-logo-white.svg",
        )
    }
    assert dashboard <= packaged, f"Missing dashboard files: {dashboard - packaged}"
    assert not {"hiraku/workflow.py", "hiraku/session.py"} & packaged
    assert not any(name.startswith("hiraku/workflow/") for name in packaged)
