"""Reusable marimo widgets and rendering for Hiraku dashboards.

Components consume prepared tables or Record objects without loading or
saving project files or querying tracking sources. The notebook owns selection state;
the optional Markdown editor owns drafts and delegates saves to a callback.
Branding stays in the Studio view.
"""

from __future__ import annotations

import html
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any
from urllib.parse import quote

import altair as alt
import marimo as mo
import pandas as pd

from ..errors import HirakuError
from ..io import dump_yaml
from ..record import Record
from .data import (
    filter_leaderboard_runs,
    lineage_records,
    plot_frame,
    run_text,
    safe_url,
    tradeoff_frame,
    tree_rows,
)


def markdown_view(
    record: Record | None,
    *,
    editor: MarkdownEditor | None = None,
    empty_message: str = "This record has no Markdown.",
    repository_url: str | None = None,
    runs: Sequence[Mapping[str, Any]] = (),
    open_run_id: str | None = None,
) -> mo.Html:
    """Render a Record's front matter and Markdown body.

    Loading, parsing, and persistence belong to the caller. Pass an editor to
    opt into editing; without one this is a read-only viewer.
    """
    if editor is not None:
        return editor.render(
            record,
            empty_message=empty_message,
            repository_url=repository_url,
            runs=runs,
            open_run_id=open_run_id,
        )
    if record is None:
        return mo.md("No record selected.")
    body = mo.md(record.markdown if record.markdown.strip() else empty_message)
    metadata = record_summary(
        record, repository_url=repository_url, runs=runs, open_run_id=open_run_id
    )
    return mo.vstack(
        [metadata, mo.Html(f'<div class="record-markdown">{body.text}</div>')]
    )


def record_summary(
    record: Record,
    *,
    repository_url: str | None = None,
    runs: Sequence[Mapping[str, Any]] = (),
    open_run_id: str | None = None,
) -> mo.Html:
    """Render local metadata and expandable details for supplied runs."""
    rows = []
    values = {"id": record.id, **record.metadata}
    for field in (
        "id",
        "state",
        "summary",
        "description",
        "goal",
        "hypothesis",
        "decision",
        "parent_id",
        "related_to",
        "commit",
        "branch",
    ):
        value = values.get(field)
        if value is None or value == "" or value == []:
            continue
        text = ", ".join(map(str, value)) if isinstance(value, list) else str(value)
        display = html.escape(text)
        if field in {"commit", "branch"} and safe_url(repository_url):
            route = "commit" if field == "commit" else "tree"
            url = f"{repository_url.rstrip('/')}/{route}/{quote(text, safe='')}"
            display = (
                f'<a href="{html.escape(url, quote=True)}" target="_blank" '
                f'rel="noopener noreferrer">{display}</a>'
            )
        rows.append(f"<dt>{field}</dt><dd>{display}</dd>")
    items = []
    any_open = False
    for run in runs:
        label = html.escape(str(run["run"]))
        is_open = run["run"] == open_run_id
        any_open |= is_open
        expanded = " open" if is_open else ""
        items.append(
            f'<li><details class="record-run"{expanded}>'
            f"<summary>{label}{_run_link(run)}</summary>"
            f"{run_details(run, show_link=False).text}</details></li>"
        )
    expanded = " open" if any_open else ""
    raw = html.escape(dump_yaml(record.metadata))
    return mo.Html(
        f'<dl class="record-metadata">{"".join(rows)}</dl>'
        f'<details class="record-runs"{expanded}><summary>Runs ({len(items)})</summary>'
        f"<ul>{''.join(items)}</ul></details>"
        '<details class="record-raw"><summary>Raw frontmatter · '
        f"{html.escape(record.id)}</summary><pre>{raw}</pre></details>"
    )


@dataclass
class _MarkdownDraft:
    record: Record
    markdown: str
    error: str = ""


class MarkdownEditor:
    """Session-local Markdown drafts with explicit Save and Cancel controls.

    Create once in a notebook cell, then pass to ``markdown_view`` in another.
    The callback persists a draft and returns the saved record. Only persisted
    records are editable; front matter remains read-only.
    """

    def __init__(
        self,
        on_save: Callable[[Record, str], Record],
        on_change: Callable[[], None] = lambda: None,
    ) -> None:
        self._on_save = on_save
        self._on_change = on_change
        self._drafts: dict[str, _MarkdownDraft] = {}
        # Marimo's widget registry holds weak references.
        self._widgets: list[Any] = []

    def _refresh(self) -> None:
        self._on_change()

    def _start(self, record: Record) -> None:
        self._drafts[record.id] = _MarkdownDraft(
            record.model_copy(deep=True), str(record.markdown)
        )
        self._refresh()

    def _cancel(self, path: str) -> None:
        self._drafts.pop(path, None)
        self._refresh()

    def _save(self, record: Record) -> None:
        draft = self._drafts[record.id]
        try:
            saved = self._on_save(draft.record, draft.markdown)
        except (HirakuError, OSError, ValueError) as exc:
            draft.error = str(exc)
            self._refresh()
            return
        record.markdown = saved.markdown
        self._cancel(record.id)

    def render(
        self,
        record: Record | None,
        *,
        empty_message: str = "This record has no Markdown.",
        repository_url: str | None = None,
        runs: Sequence[Mapping[str, Any]] = (),
        open_run_id: str | None = None,
    ) -> mo.Html:
        """Show the record or its draft, retaining drafts across selections."""
        if record is None:
            self._widgets = []
            return markdown_view(None)
        draft = self._drafts.get(record.id)
        if draft is None:
            edit = mo.ui.button(
                label="✎",
                tooltip="Edit Markdown",
                on_click=lambda _: self._start(record),
            )
            self._widgets = [edit]
            controls = mo.hstack([edit], justify="end")
            return mo.vstack(
                [
                    mo.Html(f'<div class="hiraku-markdown-controls">{controls}</div>'),
                    markdown_view(
                        record,
                        empty_message=empty_message,
                        repository_url=repository_url,
                        runs=runs,
                        open_run_id=open_run_id,
                    ),
                ]
            )
        editor = mo.ui.code_editor(
            value=draft.markdown,
            language="markdown",
            min_height=250,
            on_change=lambda value: setattr(draft, "markdown", value),
        )
        cancel = mo.ui.button(
            label="Cancel", on_click=lambda _: self._cancel(record.id)
        )
        save = mo.ui.button(
            label="Save", kind="success", on_click=lambda _: self._save(record)
        )
        self._widgets = [editor, cancel, save]
        controls = mo.hstack([cancel, save], justify="end")
        metadata = mo.Html(
            f"<pre>{html.escape(dump_yaml(draft.record.metadata))}</pre>"
        )
        message = (
            mo.callout(mo.plain_text(draft.error), kind="danger")
            if draft.error
            else mo.md("Draft — changes are saved only when you click Save.")
        )
        return mo.vstack(
            [
                mo.Html(f'<div class="hiraku-markdown-controls">{controls}</div>'),
                mo.accordion({record.id: metadata}),
                editor,
                message,
            ]
        )


def _record_label(record: Record) -> str:
    title = html.escape(record.title or record.id.rsplit("/", 1)[-1])
    badges = " ".join(
        f'<span class="badge">{html.escape(value)}</span>'
        for value in (record.type, record.state)
        if value
    )
    return (
        '<span class="hiraku-record-node">'
        '<span class="record-title">'
        f'<span class="record-symbol" aria-hidden="true"></span>{title}</span>'
        f'<span class="record-id">{html.escape(record.id)}</span>'
        f'<span class="record-badges">{badges}</span></span>'
    )


def record_tree(
    records: Sequence[Record],
    selected_id: str | None,
    on_select: Callable[[str], Any],
    *,
    matching_ids: set[str] | None = None,
) -> tuple[mo.Html, dict[str, mo.ui.button]]:
    """Render optional record navigation and return its live button references.

    Keep the buttons in a notebook variable. Selection is supplied by the
    caller so this tree can share state with other components.
    """
    children: dict[str | None, list[Record]] = {}
    for record in records:
        children.setdefault(record.parent_id, []).append(record)
    buttons: dict[str, mo.ui.button] = {}
    fragments = ["<ul>"]
    known = {record.id for record in records}
    roots = [record for record in records if record.parent_id not in known]
    # Remaining records seed components without roots, such as cycles.
    stack = [(record, False) for record in reversed(roots + list(records))]
    while stack:
        record, closing = stack.pop()
        if closing:
            fragments.append("</ul></li>")
            continue
        if record.id in buttons:
            continue
        button = mo.ui.button(
            label=_record_label(record),
            full_width=True,
            on_click=lambda _, identifier=record.id: on_select(identifier),
        )
        buttons[record.id] = button
        selected = ' aria-current="true"' if record.id == selected_id else ""
        muted = (
            " record-context"
            if matching_ids is not None and record.id not in matching_ids
            else ""
        )
        fragments.append(
            f'<li><div class="record-row{muted}"{selected}>{button.text}</div>'
        )
        nested = [
            child for child in children.get(record.id, []) if child.id not in buttons
        ]
        if nested:
            fragments.append("<ul>")
            stack.append((record, True))
            stack.extend((child, False) for child in reversed(nested))
        else:
            fragments.append("</li>")
    fragments.append("</ul>")
    content = "".join(fragments) if buttons else "No records yet."
    return (
        mo.Html(f'<nav class="record-tree" aria-label="Records">{content}</nav>'),
        buttons,
    )


def record_filters(
    records: Sequence[Record],
    on_toggle: Callable[[str, str], Any],
    *,
    types: Sequence[str] = (),
    states: Sequence[str] = (),
) -> tuple[mo.Html, dict[tuple[str, str], mo.ui.button]]:
    """Render type/state toggle chips with caller-owned filter state.

    Return live button references. Each callback identifies the field and value
    to toggle; available values always come from the unfiltered record list.
    """
    buttons = {}
    groups = []
    for field, selected in (("type", types), ("state", states)):
        chips = []
        for value in sorted(
            {getattr(record, field) for record in records if getattr(record, field)}
        ):
            active = value in selected
            label = html.escape(value)
            button = mo.ui.button(
                label=(
                    f'<span class="record-filter-chip{" active" if active else ""}">'
                    f"{'✓ ' if active else ''}{label}</span>"
                ),
                tooltip=f"{'Remove' if active else 'Add'} {field} filter: {value}",
                on_click=lambda _, field=field, value=value: on_toggle(field, value),
            )
            buttons[field, value] = button
            chips.append(button.text)
        if chips:
            groups.append(
                f'<div class="record-filter-group" role="group" '
                f'aria-label="{field.title()} filters">'
                f'<span class="record-filter-label">{field.title()}</span>'
                f"{''.join(chips)}</div>"
            )
    return mo.Html(f'<div class="record-filters">{"".join(groups)}</div>'), buttons


class LeaderboardFilters:
    """Session-local chips shared by the leaderboard plot and table."""

    def __init__(self, on_change: Callable[[], None]) -> None:
        self._active: set[str] = set()
        self._on_change = on_change
        self.buttons: dict[str, mo.ui.button] = {}

    def _toggle(self, name: str) -> None:
        self._active ^= {name}
        self._on_change()

    def clear_record_filters(self) -> None:
        """Clear the record scopes while retaining the unattributed toggle."""
        if self._active & {"record", "descendants"}:
            self._active.difference_update({"record", "descendants"})
            self._on_change()

    def render(self, record_id: str | None) -> mo.Html:
        """Render the filter row and retain the live buttons."""
        self.buttons = {}
        for name, label in (
            ("record", "Selected record"),
            ("descendants", "Descendants"),
            ("unattributed", "Include unattributed"),
        ):
            disabled = record_id is None and name != "unattributed"
            active = name in self._active and not disabled
            self.buttons[name] = mo.ui.button(
                label=(
                    f'<span class="record-filter-chip{" active" if active else ""}">'
                    f"{'✓ ' if active else ''}{label}</span>"
                ),
                disabled=disabled,
                on_click=lambda _, name=name: self._toggle(name),
            )
        return mo.Html(
            '<div class="record-filter-group leaderboard-filters" role="group" '
            'aria-label="Leaderboard filters">'
            '<span class="record-filter-label">Filter</span>'
            + "".join(button.text for button in self.buttons.values())
            + "</div>"
        )

    def apply(
        self, runs: pd.DataFrame, records: Sequence[Record], record_id: str | None
    ) -> tuple[pd.DataFrame, str | None]:
        """Return the filtered runs and the record scope for the plot caption."""
        include_record = "record" in self._active
        include_descendants = "descendants" in self._active
        include_unattributed = "unattributed" in self._active
        filtered = filter_leaderboard_runs(
            runs,
            records,
            record_id,
            include_record=include_record,
            include_descendants=include_descendants,
            include_unattributed=include_unattributed,
        )
        scope = (
            record_id
            if (include_record or include_descendants) and not include_unattributed
            else None
        )
        return filtered, scope


def filesystem_tree(
    records: Sequence[Record],
    selected_id: str | None,
    on_select: Callable[[str], Any],
    *,
    types: Sequence[str] = (),
    states: Sequence[str] = (),
) -> tuple[mo.Html, dict[str, mo.ui.button]]:
    """Navigate bundle-relative Markdown paths, independently of record lineage.

    Filters match local type and state fields; empty filters include all values.
    Directories retain matching descendants and do not select records. Keep the
    returned buttons alive and supply selection state from the caller.
    """
    children: dict[str, set[tuple[str, bool]]] = {"": set()}
    by_id = {}
    for record in records:
        if (types and record.type not in types) or (
            states and record.state not in states
        ):
            continue
        by_id[record.id] = record
        parts = record.id.split("/")
        parent = ""
        for part in parts[:-1]:
            directory = f"{parent}/{part}" if parent else part
            children.setdefault(parent, set()).add((directory, True))
            children.setdefault(directory, set())
            parent = directory
        children[parent].add((record.id, False))

    buttons: dict[str, mo.ui.button] = {}
    fragments = ["<ul>"]
    stack: list[tuple[str, bool] | str] = list(
        reversed(sorted(children[""], key=lambda item: (not item[1], item[0])))
    )
    while stack:
        node = stack.pop()
        if isinstance(node, str):
            fragments.append(node)
            continue
        path, directory = node
        label = html.escape(path.rsplit("/", 1)[-1])
        if directory:
            fragments.append(
                "<li><details open><summary>"
                f'<span class="record-folder-name">{label}</span></summary><ul>'
            )
            stack.append("</ul></details></li>")
            stack.extend(
                reversed(
                    sorted(children[path], key=lambda item: (not item[1], item[0]))
                )
            )
            continue
        record = by_id[path]
        button = mo.ui.button(
            label=_record_label(record),
            full_width=True,
            on_click=lambda _, identifier=path: on_select(identifier),
        )
        buttons[path] = button
        selected = ' aria-current="true"' if path == selected_id else ""
        fragments.append(
            f'<li><div class="record-row"{selected}>{button.text}</div></li>'
        )
    fragments.append("</ul>")
    content = "".join(fragments) if buttons else "No matching records."
    return (
        mo.Html(
            '<nav class="record-tree filesystem-tree" aria-label="Record files">'
            f"{content}</nav>"
        ),
        buttons,
    )


class RecordNavigation:
    """Session-local navigation controls delegating to the independent renderers.

    Create once, then render in a separate cell. Connect ``on_change`` to a
    Marimo state setter and read its getter in the display cell, as with
    ``MarkdownEditor``. Selection and data loading remain with the caller.
    """

    def __init__(self, on_change: Callable[[], None] = lambda: None) -> None:
        self.mode = "Files"
        self.filters: dict[str, set[str]] = {"type": set(), "state": set()}
        self._on_change = on_change
        self.mode_control: mo.ui.tabs | None = None
        self.record_buttons: dict[str, mo.ui.button] = {}
        self.filter_buttons: dict[tuple[str, str], mo.ui.button] = {}

    def _set_mode(self, value: str) -> None:
        self.mode = value
        self._on_change()

    def _toggle_filter(self, field: str, value: str) -> None:
        self.filters[field] ^= {value}
        self._on_change()

    def render(
        self,
        records: Sequence[Record],
        *,
        selected_id: str | None,
        on_select: Callable[[str], Any],
    ) -> mo.Html:
        """Render the header, filters, and active tree, retaining live widgets."""
        types = sorted(self.filters["type"])
        states = sorted(self.filters["state"])
        self.mode_control = mo.ui.tabs(
            {"Files": "", "Lineage": ""}, value=self.mode, on_change=self._set_mode
        )
        filters, self.filter_buttons = record_filters(
            records, self._toggle_filter, types=types, states=states
        )
        if self.mode == "Lineage":
            navigation, self.record_buttons = record_tree(
                lineage_records(records, types=types, states=states),
                selected_id,
                on_select,
                matching_ids={
                    record.id
                    for record in records
                    if (not types or record.type in types)
                    and (not states or record.state in states)
                },
            )
        else:
            navigation, self.record_buttons = filesystem_tree(
                records, selected_id, on_select, types=types, states=states
            )
        header = mo.Html(
            '<div class="record-navigation-header"><h2>Records</h2>'
            f'<div class="record-navigation-mode">{self.mode_control.text}</div></div>'
        )
        return mo.vstack([header, filters, navigation])


def run_table(
    records_df: pd.DataFrame,
    runs_df: pd.DataFrame,
    metric: str | None,
    initial_record: str | None = None,
    *,
    selected_key: str | None = None,
    on_select: Callable[[str | None], Any] | None = None,
    include_records: bool = True,
) -> mo.ui.table:
    """Build the selectable record/run table, retaining its hidden row keys."""
    _rows = tree_rows(records_df, runs_df, metric, include_records=include_records)

    def _style(row, column):
        style = {"fontWeight": "600"} if row["key"].startswith("record:") else {}
        if column == "Record / run":
            style["paddingLeft"] = f"{0.5 + 1.25 * row['depth']}rem"
        return style

    _initial = [
        index
        for index, row in enumerate(_rows)
        if row["key"] == (selected_key or f"record:{initial_record}")
    ]
    tree = mo.ui.table(
        _rows,
        selection="single",
        initial_selection=_initial,
        on_change=(lambda rows: on_select(rows[0]["key"] if rows else None))
        if on_select
        else None,
        hidden_columns=["key", "depth"] if _rows else None,
        page_size=50,
        show_column_summaries=False,
        show_data_types=False,
        show_download=False,
        format_mapping={
            metric: lambda value: f"{value:.4g}" if isinstance(value, float) else value
        }
        if metric
        else None,
        style_cell=lambda row, column, value: _style(_rows[int(row)], column),
    )
    return tree


def _run_link(run: Mapping[str, Any]) -> str:
    url = safe_url(run.get("url"))
    if not url:
        return ""
    source = html.escape(str(run.get("source") or "tracking backend"))
    return (
        f' <a class="run-link" href="{html.escape(url, quote=True)}" target="_blank" '
        f'rel="noopener noreferrer">Open in {source} ↗</a>'
    )


def run_details(
    selected_run: Mapping[str, Any] | None, *, show_link: bool = True
) -> mo.Html:
    """Render run provenance and a safe tracking link, or a selection prompt."""
    if selected_run is None:
        _content = mo.md("Select a run in the leaderboard to see its details.")
    else:
        _link = _run_link(selected_run) if show_link else ""
        _content = mo.Html(
            f'<div class="run-provenance">{_link}'
            f"<pre>{html.escape(run_text(selected_run))}</pre></div>"
        )
    return mo.as_html(_content)


def performance_plot(
    runs_df: pd.DataFrame,
    *,
    scope: str | None,
    metric: str | None,
    selected_run: Mapping[str, Any] | None = None,
    selected_record_id: str | None = None,
    selected_run_id: str | None = None,
    on_run_select: Callable[[str], Any] | None = None,
) -> mo.Html:
    """Plot one metric over execution order for an already scoped run table."""
    _points, _missing = plot_frame(runs_df, metric)
    _label = f"under **{scope}**" if scope else "in this leaderboard"
    _notes = [f"{len(runs_df)} runs {_label}, numbered by execution order."]
    if _missing:
        _notes.append(f"{_missing} without a numeric `{metric}` are not shown.")
    if _points.empty:
        _chart = mo.md("No runs to plot.")
    else:
        _base = alt.Chart().encode(
            x=alt.X(
                "number:Q",
                title="Run number (execution order)",
                axis=alt.Axis(format="d", tickMinStep=1),
                scale=alt.Scale(nice=False, padding=16),
            ),
            y=alt.Y("value:Q", title=metric, scale=alt.Scale(zero=False)),
        )
        _line = _base.mark_line(strokeWidth=2, opacity=0.3)
        if selected_record_id is None and selected_run is not None:
            _selected = _points.loc[_points["run"] == selected_run["run"], "record"]
            if not _selected.empty:
                selected_record_id = _selected.iloc[0]
        _cursor = "pointer" if on_run_select is not None else alt.Undefined
        if selected_run_id is None and selected_run is not None:
            selected_run_id = selected_run["run"]
        _is_selected = alt.datum.run == selected_run_id
        _dots = _base.mark_point(
            filled=True, opacity=1, cursor=_cursor, stroke="#243449"
        ).encode(
            color=_run_color(selected_record_id),
            size=alt.condition(_is_selected, alt.value(220), alt.value(90)),
            strokeWidth=alt.condition(_is_selected, alt.value(1.5), alt.value(0)),
            tooltip=[
                alt.Tooltip("number:Q", title="Run #"),
                alt.Tooltip("run:N", title="Run"),
                alt.Tooltip("record:N", title="Record"),
                alt.Tooltip("started:N", title="Started"),
                alt.Tooltip("duration:N", title="Duration"),
                alt.Tooltip("value:Q", title=metric, format=".4g"),
            ],
        )
        if on_run_select is not None:
            _dots = _dots.add_params(_run_selection())
        _spec = (
            alt.layer(_line, _dots, data=_points)
            .properties(width="container", height=340)
            .configure(background="transparent")
            .configure_view(stroke=None)
        )
        _chart = _interactive_run_chart(_spec, on_run_select)
    _content = mo.vstack([mo.md(" ".join(_notes)), _chart])
    return mo.as_html(_content)


def tradeoff_plot(
    runs_df: pd.DataFrame,
    *,
    x_metric: str | None,
    y_metric: str | None,
    x_objective: str = "minimize",
    y_objective: str = "minimize",
    selected_record_id: str | None = None,
    selected_run_id: str | None = None,
    on_run_select: Callable[[str], Any] | None = None,
) -> mo.Html:
    """Compare two metrics and highlight non-dominated runs in the supplied table."""
    if not x_metric or not y_metric:
        _comparison = mo.md("No numeric metrics are available to compare.")
    elif x_metric == y_metric:
        _comparison = mo.callout("Choose two different metrics.", kind="info")
    else:
        _points, _missing = tradeoff_frame(
            runs_df,
            x_metric,
            y_metric,
            x_objective,
            y_objective,
        )
        if _points.empty:
            _comparison = mo.callout(
                f"No runs have numeric values for both metrics ({_missing} excluded).",
                kind="info",
            )
        else:
            _highlighted = _points.loc[_points["non_dominated"]]
            _note = (
                f"**{len(_points)} comparable runs** · "
                f"**{len(_highlighted)} strong trade-offs**"
            )
            if _missing:
                _note += f" · {_missing} runs excluded for missing or non-finite values"
            _base = alt.Chart(_points).encode(
                x=alt.X(
                    "x:Q",
                    title=f"{x_metric} ({x_objective})",
                    scale=alt.Scale(zero=False),
                ),
                y=alt.Y(
                    "y:Q",
                    title=f"{y_metric} ({y_objective})",
                    scale=alt.Scale(zero=False),
                ),
                color=_run_color(selected_record_id),
                tooltip=[
                    alt.Tooltip("run:N", title="Run"),
                    alt.Tooltip("record:N", title="Record"),
                    alt.Tooltip("x:Q", title=x_metric, format=".4g"),
                    alt.Tooltip("y:Q", title=y_metric, format=".4g"),
                    alt.Tooltip("non_dominated:N", title="Strong trade-off"),
                ],
            )
            _spec = (
                _base.mark_point(
                    filled=True,
                    opacity=1,
                    stroke="#243449",
                    cursor="pointer" if on_run_select is not None else alt.Undefined,
                )
                .encode(
                    shape=alt.condition(
                        "datum.non_dominated", alt.value("diamond"), alt.value("circle")
                    ),
                    size=alt.SizeValue(
                        value=100,
                        condition=[
                            {
                                "test": str(alt.datum.run == selected_run_id),
                                "value": 340,
                            },
                            {"test": "datum.non_dominated", "value": 220},
                        ],
                    ),
                    strokeWidth=alt.condition(
                        alt.datum.run == selected_run_id, alt.value(1.5), alt.value(0)
                    ),
                )
                .properties(width="container", height=420)
                .interactive(name="pan_zoom")
                .configure(background="transparent")
                .configure_view(stroke=None)
            )
            if on_run_select is not None:
                _spec = _spec.add_params(_run_selection())
            _chart = _interactive_run_chart(_spec, on_run_select)
            _table = _highlighted.drop(columns="non_dominated").rename(
                columns={
                    "run": "Run",
                    "record": "Record",
                    "x": x_metric,
                    "y": y_metric,
                }
            )
            _comparison = mo.vstack(
                [
                    mo.md(_note),
                    mo.md(
                        "A diamond marks a strong trade-off: no comparable "
                        "run matches or improves both metrics while improving "
                        "at least one."
                    ),
                    _chart,
                    mo.ui.table(
                        _table,
                        selection="single" if on_run_select is not None else None,
                        on_change=(
                            lambda rows: (
                                on_run_select(rows["Run"].iloc[0])
                                if not rows.empty
                                else None
                            )
                        )
                        if on_run_select is not None
                        else None,
                        show_download=False,
                    ),
                ]
            )
    _content = _comparison
    return mo.as_html(_content)


def _run_color(record_id: str | None):
    default = alt.value("#4c78a8")
    if record_id is None:
        return default
    return alt.condition(alt.datum.record == record_id, alt.value("#f59e0b"), default)


def _run_selection() -> alt.Parameter:
    return alt.selection_point(name="picked_run", fields=["run"], toggle=False)


def _interactive_run_chart(
    chart: alt.Chart | alt.LayerChart, on_run_select: Callable[[str], Any] | None
) -> alt.Chart | alt.LayerChart | mo.ui.altair_chart:
    if on_run_select is None:
        return chart

    def select(rows: pd.DataFrame) -> None:
        if not rows.empty:
            on_run_select(rows["run"].iloc[0])

    return mo.ui.altair_chart(
        chart, chart_selection=False, legend_selection=False, on_change=select
    )
