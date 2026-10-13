"""Reusable branded elements for the bundled dashboard."""

from __future__ import annotations

import base64
import html
from importlib.resources import files
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from marimo import Html


def section_heading(section: str, title: str) -> Html:
    """Render a native disclosure header independent of reactive section content."""
    import marimo as mo

    return mo.Html(
        f'<details class="hk-section-heading" data-section="{html.escape(section)}">'
        f"<summary>{html.escape(title)}</summary></details>"
    )


def section_body(section: str, content: object) -> Html:
    """Mark a cell's output for the corresponding disclosure header's CSS rules."""
    import marimo as mo

    return mo.Html(
        f'<div class="hk-section-body" data-section="{html.escape(section)}">'
        f"{mo.as_html(content).text}</div>"
    )


def theme_css() -> str:
    """Read the packaged dashboard stylesheet."""
    return files("hiraku.dashboard").joinpath("theme.css").read_text(encoding="utf-8")


def logo_data_uri(*, reversed_logo: bool = False) -> str:
    """Return the packaged SDSC logo as a self-contained SVG data URI."""
    filename = "sdsc-logo-white.svg" if reversed_logo else "sdsc-logo.svg"
    content = files("hiraku.dashboard").joinpath("assets", filename).read_bytes()
    encoded = base64.b64encode(content).decode("ascii")
    return f"data:image/svg+xml;base64,{encoded}"
