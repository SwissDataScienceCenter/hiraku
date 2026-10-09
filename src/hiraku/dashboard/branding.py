"""Reusable branded elements for the bundled dashboard."""

from __future__ import annotations

import base64
from importlib.resources import files


def theme_css() -> str:
    """Read the packaged dashboard stylesheet."""
    return files("hiraku.dashboard").joinpath("theme.css").read_text(encoding="utf-8")


def logo_data_uri(*, reversed_logo: bool = False) -> str:
    """Return the packaged SDSC logo as a self-contained SVG data URI."""
    filename = "sdsc-logo-white.svg" if reversed_logo else "sdsc-logo.svg"
    content = files("hiraku.dashboard").joinpath("assets", filename).read_bytes()
    encoded = base64.b64encode(content).decode("ascii")
    return f"data:image/svg+xml;base64,{encoded}"
