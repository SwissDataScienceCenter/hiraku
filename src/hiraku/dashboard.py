from __future__ import annotations

import argparse
import ipaddress
import json
import sys
import threading
import webbrowser
from dataclasses import dataclass
from datetime import datetime
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from importlib.resources import files
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlparse

from hiraku.config import Project
from hiraku.errors import HirakuError, ValidationError
from hiraku.records import list_experiments, read_task, resolve_task
from hiraku.tracking import (
    PROVENANCE_COLUMNS,
    _available_metrics,
    annotate_commit_mismatches,
    backend_for,
    enrich_experiments,
    numeric,
)
from hiraku.util import short_commit

DASHBOARD_EXTRA = "hiraku[dashboard]"
ASSET_NAMES = {
    "/": ("index.html", "text/html; charset=utf-8"),
    "/assets/app.js": ("app.js", "text/javascript; charset=utf-8"),
    "/assets/style.css": ("style.css", "text/css; charset=utf-8"),
    "/assets/vega.min.js": ("vega.min.js", "text/javascript; charset=utf-8"),
    "/assets/vega-lite.min.js": ("vega-lite.min.js", "text/javascript; charset=utf-8"),
    "/assets/vega-embed.min.js": ("vega-embed.min.js", "text/javascript; charset=utf-8"),
}


def add_dashboard_parser(
    commands: argparse._SubParsersAction[argparse.ArgumentParser],
) -> None:
    command = commands.add_parser("dashboard", help="Open a read-only experiment dashboard")
    command.add_argument("task_id", nargs="?")
    command.add_argument("--host", default="127.0.0.1")
    command.add_argument("--port", type=int, default=0)
    command.add_argument("--no-open", action="store_true")


def require_dashboard_dependencies() -> None:
    try:
        import altair  # noqa: F401
        import bleach  # noqa: F401
        import markdown  # noqa: F401
    except ImportError as exc:
        raise HirakuError(
            f"The dashboard requires optional dependencies; install {DASHBOARD_EXTRA}."
        ) from exc


def _run_metrics(project: Project, row: dict[str, Any]) -> dict[str, Any]:
    if project.tracking_backend == "mlflow":
        return dict(row.get("metrics", {}))
    excluded = {*PROVENANCE_COLUMNS, "status", "_rank", "_commit_mismatch"}
    return {
        key: value for key, value in row.items() if key not in excluded and not key.startswith("_")
    }


def _safe_timestamp(value: Any) -> str:
    return value if isinstance(value, str) else ""


def _normalize_run(project: Project, row: dict[str, Any]) -> dict[str, Any]:
    warnings = list(row.get("_provenance_warnings", []))
    if row.get("_commit_mismatch"):
        warnings.append("Run commit does not match the experiment's bound commit.")
    return {
        "run_id": str(row.get("run_id", "")),
        "task_id": str(row.get("task_id", "")),
        "experiment_id": str(row.get("experiment_id", "")),
        "commit": str(row.get("commit", "")),
        "created_at": _safe_timestamp(row.get("created_at")),
        "status": str(row.get("status", "")),
        "params": dict(row.get("params", {})),
        "metrics": _run_metrics(project, row),
        "tags": dict(row.get("tags", {})),
        "commit_mismatch": bool(row.get("_commit_mismatch")),
        "warnings": warnings,
    }


def _parseable_timestamp(value: str) -> bool:
    if not value:
        return False
    try:
        datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return False
    return True


def render_markdown(value: str) -> str:
    require_dashboard_dependencies()
    import bleach
    import markdown

    rendered = markdown.markdown(value, extensions=["fenced_code", "tables"])
    tags = {
        *bleach.sanitizer.ALLOWED_TAGS,
        "h1",
        "h2",
        "h3",
        "h4",
        "h5",
        "h6",
        "p",
        "pre",
        "code",
        "hr",
        "br",
        "table",
        "thead",
        "tbody",
        "tr",
        "th",
        "td",
    }
    return bleach.clean(
        rendered,
        tags=tags,
        attributes={"a": ["href", "title"], "code": ["class"]},
        protocols={"http", "https", "mailto"},
        strip=True,
    )


def build_dashboard(project: Project, task_id: str) -> dict[str, Any]:
    task = read_task(project, task_id)
    records = list_experiments(project, task_id)
    loaded = backend_for(project).read_runs(task_id, allow_empty=True)
    annotated = annotate_commit_mismatches(loaded.rows, records)
    runs = [_normalize_run(project, row) for row in annotated]
    enriched = enrich_experiments(project, task_id, records, runs=loaded.rows)
    experiments: list[dict[str, Any]] = []
    for value in enriched:
        experiments.append(
            {
                key: value.get(key, "")
                for key in (
                    "id",
                    "task_id",
                    "summary",
                    "hypothesis",
                    "status",
                    "decision",
                    "commit",
                    "branch",
                    "entrypoints",
                    "conclusion",
                    "description",
                    "created_at",
                    "updated_at",
                    "run_count",
                    "parent_experiments",
                    "git_parents",
                    "branch_exists",
                    "contained_in_task_branch",
                    "commit_mismatches",
                )
            }
        )
    available = _available_metrics(project, annotated)
    declared = {metric["name"]: metric["direction"] for metric in task.metrics}
    default_metric = (
        task.metrics[0]["name"] if task.metrics else (available[0] if available else "")
    )
    directions = {name: declared.get(name, "minimize") for name in available}
    for name, direction in declared.items():
        directions[name] = direction
        if name not in available:
            available.append(name)
    available = [metric["name"] for metric in task.metrics] + sorted(
        name for name in available if name not in declared
    )
    warnings = list(loaded.warnings)
    known_experiments = {value["id"] for value in experiments}
    for run in runs:
        experiment_id = run["experiment_id"]
        if not experiment_id:
            run["association"] = "unassociated"
        elif experiment_id not in known_experiments:
            run["association"] = "unknown experiment"
            run["warnings"].append(f"Unknown experiment ID {experiment_id!r}.")
        else:
            run["association"] = "associated"
    mismatch_count = sum(run["commit_mismatch"] for run in runs)
    if mismatch_count:
        warnings.append(f"{mismatch_count} run(s) do not match their experiment's bound commit.")
    accepted_uncontained = [
        value["id"]
        for value in experiments
        if value["decision"] == "accepted" and not value["contained_in_task_branch"]
    ]
    if accepted_uncontained:
        warnings.append(
            "Accepted experiment(s) not contained in the task branch: "
            f"{', '.join(accepted_uncontained)}."
        )
    review: dict[str, str] = {}
    if task.pr_id:
        review["pr_id"] = task.pr_id
    if task.mr_id:
        review["mr_id"] = task.mr_id
    return {
        "task": {
            **task.as_dict(verbose=True),
            "markdown_html": render_markdown(task.body),
            "review": review,
        },
        "tracking_backend": project.tracking_backend,
        "experiments": experiments,
        "runs": runs,
        "metrics": available,
        "metric_directions": directions,
        "default_metric": default_metric,
        "warnings": warnings,
        "lineage": {
            "nodes": [
                {
                    "id": value["id"],
                    "status": value["status"],
                    "decision": value["decision"],
                    "branch": value["branch"],
                    "commit": value["commit"],
                    "contained_in_task_branch": value["contained_in_task_branch"],
                    "bound": bool(value["commit"]),
                }
                for value in experiments
            ],
            "edges": [
                {"source": parent, "target": value["id"]}
                for value in experiments
                for parent in value["parent_experiments"]
            ],
            "applicable": any(value["branch"] for value in experiments),
        },
    }


def _selected(values: list[str] | None, allow_unspecified: bool = False) -> set[str]:
    return {
        "" if allow_unspecified and value == "__unspecified__" else value
        for value in values or []
        if value
    }


def _metric_value(run: dict[str, Any], metric: str) -> float | None:
    return numeric(run.get("metrics", {}).get(metric))


def filter_dashboard(
    snapshot: dict[str, Any],
    metric: str | None = None,
    experiments: list[str] | None = None,
    statuses: list[str] | None = None,
) -> dict[str, Any]:
    chosen_metric = metric or snapshot["default_metric"]
    if chosen_metric and chosen_metric not in snapshot["metrics"]:
        raise ValidationError(f"Unknown dashboard metric {chosen_metric!r}.")
    experiment_ids = _selected(experiments)
    selected_statuses = _selected(statuses, allow_unspecified=True)
    experiment_by_id = {value["id"]: value for value in snapshot["experiments"]}
    visible_experiment_ids = {
        value["id"]
        for value in snapshot["experiments"]
        if (not experiment_ids or value["id"] in experiment_ids)
        and (not selected_statuses or value["status"] in selected_statuses)
    }
    if experiment_ids or selected_statuses:
        visible_runs = [
            run for run in snapshot["runs"] if run["experiment_id"] in visible_experiment_ids
        ]
    else:
        visible_runs = list(snapshot["runs"])
    direction = snapshot["metric_directions"].get(chosen_metric, "minimize")
    numeric_runs = [
        run
        for run in visible_runs
        if _metric_value(run, chosen_metric) is not None and _parseable_timestamp(run["created_at"])
    ]
    numeric_runs.sort(key=lambda run: (run["created_at"], run["run_id"]))
    frontier: list[dict[str, Any]] = []
    best: float | None = None
    for run in numeric_runs:
        value = _metric_value(run, chosen_metric)
        assert value is not None
        best = (
            value
            if best is None
            else (min(best, value) if direction == "minimize" else max(best, value))
        )
        frontier.append(
            {
                "created_at": run["created_at"],
                "run_id": run["run_id"],
                "frontier": best,
            }
        )
    visible_runs.sort(
        key=lambda run: (
            _metric_value(run, chosen_metric) is None,
            -(_metric_value(run, chosen_metric) or 0)
            if direction == "maximize"
            else (_metric_value(run, chosen_metric) or 0),
            run["created_at"],
        )
    )
    best_by_experiment: dict[str, float] = {}
    for run in numeric_runs:
        experiment_id = run["experiment_id"]
        value = _metric_value(run, chosen_metric)
        assert value is not None
        current = best_by_experiment.get(experiment_id)
        best_by_experiment[experiment_id] = (
            value
            if current is None
            else (min(current, value) if direction == "minimize" else max(current, value))
        )
    experiment_rows = [
        {
            **value,
            "short_commit": short_commit(value["commit"]),
            "best_metric": best_by_experiment.get(value["id"]),
        }
        for value in snapshot["experiments"]
    ]
    points = [
        {
            "created_at": run["created_at"],
            "metric_value": _metric_value(run, chosen_metric),
            "metric": chosen_metric,
            "experiment_id": run["experiment_id"],
            "experiment_summary": experiment_by_id.get(run["experiment_id"], {}).get("summary", ""),
            "experiment_status": experiment_by_id.get(run["experiment_id"], {}).get("status", ""),
            "experiment_decision": experiment_by_id.get(run["experiment_id"], {}).get(
                "decision", ""
            ),
            "run_id": run["run_id"],
            "commit": run["commit"],
            "run_status": run["status"],
            "commit_mismatch": run["commit_mismatch"],
        }
        for run in numeric_runs
    ]
    return {
        "metric": chosen_metric,
        "direction": direction,
        "runs": visible_runs,
        "experiments": experiment_rows,
        "frontier": frontier,
        "chart_spec": build_chart_spec(points, frontier, chosen_metric, direction),
        "counts": {
            "experiments": len(visible_experiment_ids),
            "runs": len(visible_runs),
            "numeric_runs": len(numeric_runs),
            "warnings": len(snapshot["warnings"]),
        },
    }


def build_chart_spec(
    points: list[dict[str, Any]],
    frontier: list[dict[str, Any]],
    metric: str,
    direction: str,
) -> dict[str, Any]:
    require_dashboard_dependencies()
    import altair as alt

    point_chart = (
        alt.Chart(alt.Data(values=points))
        .mark_point(size=90, opacity=0.88, filled=True)
        .encode(
            x=alt.X("created_at:T", title="Run time"),
            y=alt.Y("metric_value:Q", title=metric or "Metric"),
            color=alt.Color("experiment_id:N", title="Experiment"),
            shape=alt.Shape("commit_mismatch:N", title="Commit mismatch"),
            tooltip=[
                alt.Tooltip("run_id:N", title="Run"),
                alt.Tooltip("experiment_id:N", title="Experiment"),
                alt.Tooltip("experiment_summary:N", title="Summary"),
                alt.Tooltip("experiment_status:N", title="Experiment status"),
                alt.Tooltip("experiment_decision:N", title="Decision"),
                alt.Tooltip("metric_value:Q", title=metric, format=".6g"),
                alt.Tooltip("created_at:T", title="Created"),
                alt.Tooltip("commit:N", title="Commit"),
                alt.Tooltip("run_status:N", title="Run status"),
                alt.Tooltip("commit_mismatch:N", title="Commit mismatch"),
            ],
        )
    )
    frontier_chart = (
        alt.Chart(alt.Data(values=frontier))
        .mark_line(interpolate="step-after", color="#de6b48", strokeWidth=3)
        .encode(
            x=alt.X("created_at:T", title="Run time"),
            y=alt.Y("frontier:Q", title=metric or "Metric"),
            tooltip=[
                alt.Tooltip("frontier:Q", title=f"Frontier ({direction})", format=".6g"),
                alt.Tooltip("created_at:T", title="Created"),
                alt.Tooltip("run_id:N", title="Run"),
            ],
        )
    )
    return (
        alt.layer(point_chart, frontier_chart)
        .properties(width="container", height=360, title=f"{metric or 'Metric'} over time")
        .configure_view(stroke=None)
        .configure_axis(labelColor="#435064", titleColor="#243047", gridColor="#e6e9ef")
        .to_dict()
    )


def _is_loopback(host: str) -> bool:
    if host.lower() == "localhost":
        return True
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


@dataclass
class DashboardState:
    project: Project
    task_id: str
    snapshot: dict[str, Any]
    lock: threading.Lock

    def refresh(self) -> dict[str, Any]:
        value = build_dashboard(self.project, self.task_id)
        with self.lock:
            self.snapshot = value
        return value

    def current(self) -> dict[str, Any]:
        with self.lock:
            return self.snapshot


def _asset_path(name: str) -> Path:
    return Path(str(files("hiraku").joinpath("resources", "dashboard", name)))


def dashboard_handler(state: DashboardState) -> type[BaseHTTPRequestHandler]:
    class Handler(BaseHTTPRequestHandler):
        server_version = "HirakuDashboard/0.2"

        def log_message(self, format: str, *args: Any) -> None:
            return

        def _json(self, value: Any, status: HTTPStatus = HTTPStatus.OK) -> None:
            payload = json.dumps(value, ensure_ascii=False, allow_nan=False).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(payload)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.end_headers()
            self.wfile.write(payload)

        def _error(self, message: str, status: HTTPStatus) -> None:
            self._json({"error": message}, status)

        def do_GET(self) -> None:  # noqa: N802
            parsed = urlparse(self.path)
            if parsed.path in ASSET_NAMES:
                name, content_type = ASSET_NAMES[parsed.path]
                path = _asset_path(name)
                if not path.is_file():
                    self._error("Dashboard asset is missing.", HTTPStatus.INTERNAL_SERVER_ERROR)
                    return
                payload = path.read_bytes()
                self.send_response(HTTPStatus.OK)
                self.send_header("Content-Type", content_type)
                self.send_header("Content-Length", str(len(payload)))
                self.send_header("X-Content-Type-Options", "nosniff")
                self.send_header(
                    "Content-Security-Policy",
                    "default-src 'self'; script-src 'self' 'unsafe-eval'; "
                    "style-src 'self' 'unsafe-inline'; "
                    "connect-src 'self'; img-src 'self' data:",
                )
                self.end_headers()
                self.wfile.write(payload)
                return
            if parsed.path == "/api/snapshot":
                self._json(state.current())
                return
            if parsed.path == "/api/view":
                query = parse_qs(parsed.query)
                try:
                    value = filter_dashboard(
                        state.current(),
                        metric=(query.get("metric") or [None])[0],
                        experiments=query.get("experiment"),
                        statuses=query.get("status"),
                    )
                except HirakuError as exc:
                    self._error(str(exc), HTTPStatus.BAD_REQUEST)
                    return
                self._json(value)
                return
            if parsed.path == "/api/refresh":
                try:
                    value = state.refresh()
                except HirakuError:
                    self._error(
                        "Could not refresh dashboard data; check the Hiraku terminal for details.",
                        HTTPStatus.INTERNAL_SERVER_ERROR,
                    )
                    return
                self._json(value)
                return
            self._error("Not found.", HTTPStatus.NOT_FOUND)

        def do_POST(self) -> None:  # noqa: N802
            self._error("The Hiraku dashboard is read-only.", HTTPStatus.METHOD_NOT_ALLOWED)

        do_PUT = do_POST
        do_PATCH = do_POST
        do_DELETE = do_POST

    return Handler


def create_dashboard_server(
    project: Project, task_id: str, host: str, port: int
) -> tuple[ThreadingHTTPServer, DashboardState]:
    if not 0 <= port <= 65535:
        raise ValidationError("--port must be between 0 and 65535.")
    snapshot = build_dashboard(project, task_id)
    state = DashboardState(project, task_id, snapshot, threading.Lock())
    try:
        server = ThreadingHTTPServer((host, port), dashboard_handler(state))
    except OSError as exc:
        raise HirakuError(f"Could not start dashboard server on {host}:{port}: {exc}.") from exc
    return server, state


def run_dashboard(project: Project, args: argparse.Namespace) -> int:
    require_dashboard_dependencies()
    task_id = resolve_task(project, args.task_id)
    read_task(project, task_id)
    if not _is_loopback(args.host):
        print(
            "Warning: dashboard is exposed beyond loopback without authentication or TLS.",
            file=sys.stderr,
        )
    server, _ = create_dashboard_server(project, task_id, args.host, args.port)
    bound_host, bound_port = server.server_address[:2]
    display_host = "127.0.0.1" if bound_host in {"0.0.0.0", "::"} else bound_host
    url = f"http://{display_host}:{bound_port}/"
    print(f"Hiraku dashboard for {task_id}: {url}", flush=True)
    if not args.no_open:
        webbrowser.open(url)
    try:
        server.serve_forever(poll_interval=0.2)
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
    print("Hiraku dashboard stopped.")
    return 0
