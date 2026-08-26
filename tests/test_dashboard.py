from __future__ import annotations

import builtins
import csv
import json
import threading
import urllib.error
import urllib.request
from pathlib import Path

import pytest
from conftest import hiraku, run

pytest.importorskip("altair")
pytest.importorskip("bleach")
pytest.importorskip("markdown")

from hiraku.config import load_project
from hiraku.dashboard import (
    _asset_path,
    _is_loopback,
    build_dashboard,
    create_dashboard_server,
    filter_dashboard,
    render_markdown,
    require_dashboard_dependencies,
)
from hiraku.errors import HirakuError


def configure_task(repository: Path, task_id: str = "heart-disease") -> None:
    hiraku(repository, "task", "create", task_id)
    run(repository, "git", "switch", f"hiraku/task/{task_id}")
    task_path = repository / f"tasks/{task_id}/task.md"
    task_path.write_text(
        task_path.read_text(encoding="utf-8")
        .replace('summary: ""', 'summary: "Improve heart-disease risk prediction"')
        .replace('goal: ""', 'goal: "Minimize validation log loss."')
        .replace(
            "metrics: []",
            "metrics:\n"
            "  - name: val/log_loss\n"
            "    direction: minimize\n"
            "  - name: val/accuracy\n"
            "    direction: maximize",
        )
        .replace("# Heart Disease", "# Heart Disease\n\n<script>alert('unsafe')</script>"),
        encoding="utf-8",
    )
    run(repository, "git", "add", ".")
    run(repository, "git", "commit", "-m", "create dashboard task")


def add_experiment(repository: Path, task_id: str, experiment_id: str, status: str) -> str:
    hiraku(
        repository,
        "exp",
        "create",
        f"{task_id}:{experiment_id}",
        "--summary",
        experiment_id.replace("-", " ").title(),
        "--hypothesis",
        f"Test {experiment_id}",
        "--status",
        status,
    )
    run(repository, "git", "add", ".")
    run(repository, "git", "commit", "-m", f"record {experiment_id}")
    commit = run(repository, "git", "rev-parse", "HEAD").stdout.strip()
    hiraku(repository, "exp", "set", f"{task_id}:{experiment_id}", "--commit", commit)
    return commit


def write_runs(repository: Path, task_id: str, rows: list[dict[str, str]]) -> None:
    path = repository / f"tasks/{task_id}/runs.csv"
    with path.open("w", encoding="utf-8", newline="") as stream:
        writer = csv.DictWriter(
            stream,
            fieldnames=[
                "run_id",
                "task_id",
                "experiment_id",
                "commit",
                "created_at",
                "val/log_loss",
                "val/accuracy",
            ],
        )
        writer.writeheader()
        writer.writerows(rows)


def dashboard_fixture(repository: Path) -> tuple[str, str]:
    task_id = "heart-disease"
    configure_task(repository, task_id)
    logistic_commit = add_experiment(repository, task_id, "logistic-baseline", "evaluated")
    forest_commit = add_experiment(repository, task_id, "random-forest", "active")
    write_runs(
        repository,
        task_id,
        [
            {
                "run_id": "run-logistic",
                "task_id": task_id,
                "experiment_id": "logistic-baseline",
                "commit": logistic_commit,
                "created_at": "2026-08-26T10:00:00.000Z",
                "val/log_loss": "0.41",
                "val/accuracy": "0.82",
            },
            {
                "run_id": "run-forest",
                "task_id": task_id,
                "experiment_id": "random-forest",
                "commit": forest_commit,
                "created_at": "2026-08-26T11:00:00.000Z",
                "val/log_loss": "0.36",
                "val/accuracy": "0.85",
            },
            {
                "run_id": "run-unassociated",
                "task_id": task_id,
                "experiment_id": "",
                "commit": "",
                "created_at": "2026-08-26T12:00:00.000Z",
                "val/log_loss": "0.33",
                "val/accuracy": "0.86",
            },
        ],
    )
    return task_id, forest_commit


def test_dashboard_builds_backend_neutral_snapshot(repository: Path) -> None:
    task_id, _ = dashboard_fixture(repository)
    snapshot = build_dashboard(load_project(repository), task_id)
    assert snapshot["default_metric"] == "val/log_loss"
    assert snapshot["metric_directions"] == {
        "val/log_loss": "minimize",
        "val/accuracy": "maximize",
    }
    assert len(snapshot["experiments"]) == 2
    assert len(snapshot["runs"]) == 3
    assert snapshot["runs"][-1]["association"] == "unassociated"
    assert "<script" not in snapshot["task"]["markdown_html"]
    assert "alert('unsafe')" in snapshot["task"]["markdown_html"]


def test_frontier_recomputes_from_visible_runs_and_direction(repository: Path) -> None:
    task_id, _ = dashboard_fixture(repository)
    snapshot = build_dashboard(load_project(repository), task_id)

    complete = filter_dashboard(snapshot)
    assert [point["frontier"] for point in complete["frontier"]] == [0.41, 0.36, 0.33]
    assert complete["runs"][0]["run_id"] == "run-unassociated"

    evaluated = filter_dashboard(snapshot, statuses=["evaluated"])
    assert [run["run_id"] for run in evaluated["runs"]] == ["run-logistic"]
    assert [point["frontier"] for point in evaluated["frontier"]] == [0.41]

    accuracy = filter_dashboard(snapshot, metric="val/accuracy")
    assert accuracy["direction"] == "maximize"
    assert [point["frontier"] for point in accuracy["frontier"]] == [0.82, 0.85, 0.86]
    assert accuracy["chart_spec"]["$schema"].endswith("vega-lite/v6.4.1.json")


def test_dashboard_server_is_read_only_and_serves_bundled_assets(repository: Path) -> None:
    task_id, _ = dashboard_fixture(repository)
    server, _ = create_dashboard_server(load_project(repository), task_id, "127.0.0.1", 0)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    base = f"http://127.0.0.1:{server.server_address[1]}"
    try:
        with urllib.request.urlopen(f"{base}/", timeout=5) as response:
            assert response.status == 200
            assert b"Hiraku dashboard" in response.read()
            assert "default-src 'self'" in response.headers["Content-Security-Policy"]
        with urllib.request.urlopen(f"{base}/assets/vega.min.js", timeout=5) as response:
            assert response.status == 200
            assert len(response.read()) > 100_000
        with urllib.request.urlopen(f"{base}/api/snapshot", timeout=5) as response:
            value = json.loads(response.read())
            assert value["task"]["id"] == task_id
        request = urllib.request.Request(f"{base}/api/refresh", method="POST")
        with pytest.raises(urllib.error.HTTPError) as raised:
            urllib.request.urlopen(request, timeout=5)
        assert raised.value.code == 405
        assert b"read-only" in raised.value.read()
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


def test_dashboard_assets_are_complete_and_markdown_is_sanitized() -> None:
    for name in (
        "index.html",
        "app.js",
        "style.css",
        "vega.min.js",
        "vega-lite.min.js",
        "vega-embed.min.js",
    ):
        assert _asset_path(name).stat().st_size > 0
    rendered = render_markdown(
        '# Safe\n\n<a href="javascript:alert(1)">bad</a><img src=x onerror=alert(1)>'
    )
    assert "javascript:" not in rendered
    assert "onerror" not in rendered
    assert "<img" not in rendered


def test_dashboard_cli_resolves_before_starting_server(repository: Path) -> None:
    result = hiraku(
        repository,
        "dashboard",
        "missing",
        "--no-open",
        check=False,
    )
    assert result.returncode == 2
    assert "Task 'missing' does not exist" in result.stderr


def test_dashboard_dependency_error_and_host_classification(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    original_import = builtins.__import__

    def missing_altair(name: str, *args: object, **kwargs: object) -> object:
        if name == "altair":
            raise ImportError("not installed")
        return original_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", missing_altair)
    with pytest.raises(HirakuError, match=r"install hiraku\[dashboard\]"):
        require_dashboard_dependencies()
    assert _is_loopback("127.0.0.1") is True
    assert _is_loopback("localhost") is True
    assert _is_loopback("0.0.0.0") is False
