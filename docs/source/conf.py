from pathlib import Path

project = "hiraku"
copyright = "2026, hiraku contributors"
author = "hiraku contributors"

extensions = [
    "breathe",
    "myst_parser",
]

source_suffix = {
    ".rst": "restructuredtext",
    ".md": "markdown",
}

myst_enable_extensions = [
    "dollarmath",
]

_docs_root = Path(__file__).resolve().parents[1]

breathe_projects = {
    "hiraku": str(_docs_root / "_doxygen" / "xml"),
}
breathe_default_project = "hiraku"

templates_path = ["_templates"]
exclude_patterns = []

html_theme = "furo"
html_title = "HIRAKU"
html_static_path = []
