"""Interactive terminal views for Hiraku jobs and leaderboard runs."""

from __future__ import annotations

import json
import os
import shlex
import tempfile
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict
from rich.console import Group
from rich.panel import Panel
from rich.syntax import Syntax
from rich.text import Text
from textual import work
from textual.app import App, ComposeResult
from textual.containers import Horizontal, Vertical
from textual.message import Message
from textual.screen import ModalScreen
from textual.theme import Theme
from textual.widgets import (
    DataTable,
    Footer,
    Header,
    Input,
    Static,
    TabbedContent,
    TabPane,
)
from textual.worker import get_current_worker

from hiraku.job import Submission, observe_submission
from hiraku.project import Project
from hiraku.tracking.leaderboard import Leaderboard


class Settings(BaseModel):
    """Persistent, project-local TUI preferences."""

    model_config = ConfigDict(extra="ignore")

    theme: str = "textual-dark"

    @classmethod
    def load(cls, path: Path) -> Settings:
        if not path.exists():
            return cls()

        try:
            return cls.model_validate_json(path.read_text())
        except (OSError, ValueError):
            return cls()

    def save(self, path: Path) -> None:
        """Atomically replace the settings file."""
        path.parent.mkdir(parents=True, exist_ok=True)

        temporary: str | None = None
        try:
            with tempfile.NamedTemporaryFile(
                mode="w",
                encoding="utf-8",
                dir=path.parent,
                prefix=".tui-",
                suffix=".tmp",
                delete=False,
            ) as file:
                temporary = file.name
                file.write(self.model_dump_json(indent=2) + "\n")

            os.replace(temporary, path)
        finally:
            if temporary is not None:
                Path(temporary).unlink(missing_ok=True)


def local_time(value: str | None) -> str:
    if not value:
        return "—"
    try:
        return datetime.fromisoformat(value).astimezone().strftime("%Y-%m-%d %H:%M:%S")
    except ValueError:
        return value


def command_text(spec: dict[str, Any]) -> str:
    return shlex.join(
        [
            str(spec["executable"]),
            *(str(arg) for arg in spec.get("arguments", [])),
        ]
    )


def job_state(state: str) -> Text:
    colors = {
        "COMPLETED": "green",
        "FAILED": "red",
        "CANCELED": "red",
        "ACTIVE": "cyan",
        "QUEUED": "yellow",
        "UNKNOWN": "yellow",
    }
    return Text(state, style=colors.get(state, "default"))


class FilterChanged(Message):
    """A live filter value emitted by the search overlay."""

    def __init__(self, kind: str, value: str) -> None:
        super().__init__()
        self.kind = kind
        self.value = value


class FilterScreen(ModalScreen[None]):
    """Small focused search overlay for the active table."""

    BINDINGS = [("escape", "cancel", "Close")]

    def __init__(self, kind: str, value: str) -> None:
        super().__init__()
        self.kind = kind
        self.value = value

    def compose(self) -> ComposeResult:
        label = "jobs" if self.kind == "job" else "runs"
        with Vertical(id="filter-dialog"):
            yield Static(f"Filter {label} · Enter or Esc to close")
            yield Input(
                value=self.value,
                placeholder=f"Search {label}...",
                id="filter-query",
            )

    def on_mount(self) -> None:
        self.query_one("#filter-query", Input).focus()

    def on_input_changed(self, event: Input.Changed) -> None:
        if event.input.id == "filter-query":
            self.post_message(FilterChanged(self.kind, event.value))

    def on_input_submitted(self, event: Input.Submitted) -> None:
        if event.input.id == "filter-query":
            self.dismiss()

    def action_cancel(self) -> None:
        self.dismiss()


class HirakuApp(App[None]):
    """Explore Hiraku submissions and leaderboard runs."""

    TITLE = "Hiraku"

    CSS = """
    Screen {
        layout: vertical;
    }

    #content {
        height: 1fr;
        margin: 0 1;
    }

    .browser {
        height: 1fr;
        padding: 0 1 1 1;
    }

    .browser DataTable {
        width: 2fr;
        height: 1fr;
        border: round $panel;
    }

    .browser DataTable:focus {
        border: round $accent;
    }

    .details {
        width: 1fr;
        min-width: 28;
        height: 1fr;
        padding: 1 2;
        border: round $accent;
        border-title-color: $accent;
        border-title-style: bold;
        overflow-y: auto;
    }

    #project-path {
        height: 1;
        margin: 0 2;
        color: $text-muted;
        text-overflow: ellipsis;
    }

    #overview {
        height: 5;
        margin: 0 1;
    }

    .metric {
        width: 1fr;
        height: 5;
        margin: 0 1;
        padding: 0 1;
        border: round $primary;
        background: $panel;
        content-align: center middle;
    }

    #metric-active {
        border: round $accent;
    }

    #metric-failed {
        border: round $error;
    }

    #metric-runs {
        border: round $success;
    }

    FilterScreen {
        align: center middle;
        background: $background 70%;
    }

    #filter-dialog {
        width: 70%;
        max-width: 80;
        height: auto;
        padding: 1 2;
        border: round $accent;
        background: $surface;
    }

    #filter-query {
        margin-top: 1;
    }
    """

    BINDINGS = [
        ("r", "refresh", "Refresh"),
        ("ctrl+f", "focus_filter", "Filter"),
        ("q", "quit", "Quit"),
    ]

    def __init__(self, project: Project) -> None:
        super().__init__()

        self.project = project
        self.settings_path = project.root / ".hiraku" / "tui.json"
        self.settings = Settings.load(self.settings_path)

        self._jobs: list[dict[str, Any]] = []
        self._runs: list[dict[str, Any]] = []
        self._table_filters = {"job": "", "run": ""}

        self._job_lookup: dict[str, dict[str, Any]] = {}
        self._run_lookup: dict[str, dict[str, Any]] = {}

        self.theme = (
            self.settings.theme
            if self.settings.theme in self.available_themes
            else "textual-dark"
        )

    def compose(self) -> ComposeResult:
        yield Header(show_clock=True)
        yield Static(str(self.project.root), id="project-path")
        with Horizontal(id="overview"):
            yield Static(id="metric-jobs", classes="metric")
            yield Static(id="metric-active", classes="metric")
            yield Static(id="metric-failed", classes="metric")
            yield Static(id="metric-runs", classes="metric")

        with TabbedContent(id="content"):
            with TabPane("Jobs", id="jobs-pane"), Horizontal(classes="browser"):
                yield DataTable(
                    id="job-table",
                    cursor_type="row",
                )
                yield Static(
                    "Select a job to inspect it",
                    id="job-details",
                    classes="details",
                )

            with TabPane("Runs", id="runs-pane"), Horizontal(classes="browser"):
                yield DataTable(
                    id="run-table",
                    cursor_type="row",
                )
                yield Static(
                    "Select a run to inspect it",
                    id="run-details",
                    classes="details",
                )

        yield Footer()

    def on_mount(self) -> None:
        self.query_one("#job-details", Static).border_title = "JOB INSPECTOR"
        self.query_one("#run-details", Static).border_title = "RUN INSPECTOR"

        jobs = self.query_one("#job-table", DataTable)
        jobs.zebra_stripes = True
        jobs.show_row_labels = False
        jobs.border_title = "SUBMISSIONS"
        jobs.border_subtitle = "newest first"
        jobs.add_columns(
            "ID",
            "Record",
            "Submitted",
            "Executor",
            "State",
        )
        runs = self.query_one("#run-table", DataTable)
        runs.zebra_stripes = True
        runs.show_row_labels = False
        runs.border_title = "LEADERBOARD RUNS"
        runs.border_subtitle = "newest first"
        runs.add_columns(
            "ID",
            "Record",
            "Timestamp",
            "Duration",
        )

        # Also captures changes from Ctrl+P's theme picker.
        self.theme_changed_signal.subscribe(self, self._on_theme_changed)

        self._update_overview()
        self.load_runs()
        self.load_jobs()

    # --------------------------------------------------
    # Settings and themes
    # --------------------------------------------------

    def _on_theme_changed(self, theme: Theme) -> None:
        self.settings.theme = theme.name
        try:
            self.settings.save(self.settings_path)
        except OSError as exc:
            self.notify(
                f"Could not save settings: {exc}",
                severity="warning",
            )

    # --------------------------------------------------
    # Refresh and loading
    # --------------------------------------------------

    def action_refresh(self) -> None:
        self.load_jobs()
        self.load_runs()

    def action_focus_filter(self) -> None:
        active_tab = self.query_one("#content", TabbedContent).active
        kind = "job" if active_tab == "jobs-pane" else "run"
        self.push_screen(FilterScreen(kind, self._table_filters[kind]))

    @work(thread=True, exclusive=True, group="jobs")
    def load_jobs(self) -> None:
        try:
            submissions = Submission.list(self.project)
            rows = [observe_submission(item, self.project) for item in submissions]
            error = None
        except Exception as exc:
            rows = []
            error = str(exc)

        if not get_current_worker().is_cancelled:
            self.call_from_thread(self._show_jobs, rows, error)

    def _show_jobs(
        self,
        rows: list[dict[str, Any]],
        error: str | None,
    ) -> None:
        self._jobs = rows
        self._job_lookup = {row["id"]: row for row in rows}

        self.query_one("#job-details", Static).update(
            Text(f"Jobs error: {error}" if error else "Select a job to inspect it")
        )

        self._update_overview()
        self._filter_table("job")

    @work(thread=True, exclusive=True, group="runs")
    def load_runs(self) -> None:
        try:
            names = Leaderboard.list(self.project)
            if not names:
                raise ValueError("No leaderboards found in this project")
            board = Leaderboard.load(self.project, names[0])
            runs = board.list_runs(self.project)
            metrics = board.metric_names(runs)
            runs.sort(
                key=lambda run: (
                    run.timestamp is not None,
                    run.timestamp or datetime.min.replace(tzinfo=UTC),
                    run.id,
                ),
                reverse=True,
            )

            records = {
                item.id: item.title or item.id for item in self.project.vault.list()
            }

            rows = [
                {
                    "id": run.id,
                    "record": records.get(
                        run.record_id,
                        run.record_id or "—",
                    ),
                    "timestamp": (
                        run.timestamp.astimezone().strftime("%Y-%m-%d %H:%M:%S")
                        if run.timestamp
                        else "—"
                    ),
                    "duration": run.duration,
                    "metrics": {metric: run.metrics.get(metric) for metric in metrics},
                    "params": dict(run.params),
                    "commit": run.commit,
                    "job_id": run.job_id,
                    "url": run.url,
                }
                for run in runs
            ]
            error = None
        except Exception as exc:
            rows = []
            error = str(exc)

        if not get_current_worker().is_cancelled:
            self.call_from_thread(self._show_runs, rows, error)

    def _show_runs(
        self,
        rows: list[dict[str, Any]],
        error: str | None,
    ) -> None:
        self._runs = rows
        self._run_lookup = {row["id"]: row for row in rows}

        self.query_one("#run-details", Static).update(
            Text(
                f"Leaderboard error: {error}" if error else "Select a run to inspect it"
            )
        )

        self._update_overview()
        self._filter_table("run")

    def _update_overview(self) -> None:
        states = [row["status"]["state"] for row in self._jobs]
        active = sum(state in {"ACTIVE", "QUEUED"} for state in states)
        unsuccessful = sum(state in {"FAILED", "CANCELED"} for state in states)
        values = (
            ("#metric-jobs", "SUBMISSIONS", len(self._jobs), "bold cyan"),
            ("#metric-active", "IN FLIGHT", active, "bold yellow"),
            ("#metric-failed", "UNSUCCESSFUL", unsuccessful, "bold red"),
            ("#metric-runs", "TRACKED RUNS", len(self._runs), "bold green"),
        )
        for selector, label, value, style in values:
            self.query_one(selector, Static).update(
                Group(
                    Text(label, style="dim"),
                    Text(str(value), style=style),
                )
            )

    # --------------------------------------------------
    # Events
    # --------------------------------------------------

    def on_filter_changed(self, event: FilterChanged) -> None:
        self._table_filters[event.kind] = event.value
        self._filter_table(event.kind)

    # --------------------------------------------------
    # Tables and details
    # --------------------------------------------------

    def _filter_table(self, kind: str) -> None:
        table = self.query_one(f"#{kind}-table", DataTable)
        query = self._table_filters[kind].casefold().strip()

        rows = self._jobs if kind == "job" else self._runs
        table.clear()

        for item in rows:
            if kind == "job":
                state = item["status"]["state"]

                values = (
                    item["id"][:8],
                    item.get("record_id") or "—",
                    local_time(item.get("submitted_at")),
                    item["executor"],
                    job_state(state),
                )
            else:
                values = (
                    item["id"][:12],
                    str(item["record"]),
                    str(item["timestamp"]),
                    (str(item["duration"]) if item["duration"] is not None else "—"),
                )

            searchable_values = [str(value) for value in values]
            if kind == "run":
                searchable_values.extend(
                    f"{key}={value}" for key, value in item["metrics"].items()
                )
            searchable = " ".join(searchable_values).casefold()

            if not query or query in searchable:
                table.add_row(
                    *values,
                    key=item["id"],
                )

    def on_data_table_row_selected(self, event: DataTable.RowSelected) -> None:
        self._show_selected_row(event.data_table, event.row_key.value)

    def on_data_table_row_highlighted(self, event: DataTable.RowHighlighted) -> None:
        self._show_selected_row(event.data_table, event.row_key.value)

    def _show_selected_row(self, table: DataTable, row_id: str | None) -> None:
        if row_id is None:
            return

        item_id = str(row_id)

        if table.id == "job-table":
            item = self._job_lookup.get(item_id)
            if item is not None:
                self._show_job_details(item)

        elif table.id == "run-table":
            item = self._run_lookup.get(item_id)
            if item is not None:
                self._show_run_details(item)

    def _show_job_details(self, item: dict[str, Any]) -> None:
        status = item["status"]

        summary = (
            f"Job: {item['id']}\n"
            f"State: {status['state']}\n"
            f"Executor: {item['executor']}\n"
            f"Record: {item.get('record_id') or '—'}\n"
            f"Submitted: {local_time(item.get('submitted_at'))}"
        )

        self.query_one("#job-details", Static).update(
            Group(
                Text(summary),
                Panel(
                    Syntax(
                        command_text(item["spec"]),
                        "bash",
                        word_wrap=True,
                    ),
                    title="Command",
                    border_style="cyan",
                ),
                Text(status.get("message") or ""),
            )
        )

    def _show_run_details(self, item: dict[str, Any]) -> None:
        metrics = json.dumps(item["metrics"], indent=2, default=str)
        params = json.dumps(item["params"], indent=2, default=str)

        summary = (
            f"Run: {item['id']}\n"
            f"Record: {item['record']}\n"
            f"Job: {item['job_id'] or '—'}\n"
            f"Commit: {item['commit'] or '—'}\n"
            f"Timestamp: {item['timestamp']}\n"
            f"Duration: {item['duration'] or '—'}\n"
            f"URL: {item['url'] or '—'}"
        )

        self.query_one("#run-details", Static).update(
            Group(
                Text(summary),
                Panel(
                    Syntax(metrics, "json", word_wrap=True),
                    title="Metrics",
                    border_style="cyan",
                ),
                Panel(
                    Syntax(params, "json", word_wrap=True),
                    title="Parameters",
                    border_style="green",
                ),
            )
        )
