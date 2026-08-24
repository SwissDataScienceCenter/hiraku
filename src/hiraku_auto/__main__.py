"""Entry point for `python -m hiraku_auto`.

Lets the CLI run straight from a checkout without installing anything:

    PYTHONPATH=src python3 -m hiraku_auto tree

Installing (`uv sync` or `pip install -e .`) puts the `hiraku-auto` script on PATH, which is the
normal way to use it; this exists so a clone works before that.
"""

from .cli import main

raise SystemExit(main())
