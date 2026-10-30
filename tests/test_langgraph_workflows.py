from __future__ import annotations

from pathlib import Path

import pytest

from hiraku.config import HirakuConfig
from hiraku.errors import ValidationError, WorkflowError
from hiraku.workflows.langgraph import (
    resume_workflow,
    start_workflow,
    workflow_status,
)


def _write_workflow(config: HirakuConfig, source: str) -> None:
    directory = config.workflows_dir / "broken"
    directory.mkdir()
    (directory / "workflow.py").write_text(source, encoding="utf-8")


@pytest.mark.parametrize(
    ("source", "message"),
    [
        ("def build_graph(checkpointer, *, config): pass\n", "input_schema()"),
        (
            "def input_schema(): return {'type': 'array'}\n"
            "def build_graph(checkpointer, *, config): pass\n",
            "JSON Schema object",
        ),
        (
            "def input_schema(): raise RuntimeError('schema failed')\n"
            "def build_graph(checkpointer, *, config): pass\n",
            "schema failed",
        ),
    ],
)
def test_start_rejects_invalid_workflow_input_schema(
    tmp_path: Path, source: str, message: str
) -> None:
    config = HirakuConfig.init(tmp_path, install_skill=False)
    _write_workflow(config, source)

    with pytest.raises(WorkflowError, match=message):
        start_workflow("broken", config=config)


@pytest.mark.parametrize(
    ("factory", "message"),
    [
        ("", "must export build_graph"),
        (
            "def build_graph(checkpointer, *, config):\n"
            "    raise RuntimeError('graph failed')\n",
            "Cannot build LangGraph workflow.*graph failed",
        ),
        (
            "def build_graph(checkpointer, *, config):\n    return object()\n",
            "must return a compiled graph",
        ),
    ],
)
def test_start_rejects_invalid_graph_factory(
    tmp_path: Path, factory: str, message: str
) -> None:
    config = HirakuConfig.init(tmp_path, install_skill=False)
    _write_workflow(
        config,
        "def input_schema(): return {'type': 'object'}\n" + factory,
    )

    with pytest.raises(WorkflowError, match=message):
        start_workflow("broken", config=config)


def test_start_wraps_workflow_import_errors(tmp_path: Path) -> None:
    config = HirakuConfig.init(tmp_path, install_skill=False)
    _write_workflow(config, "def input_schema():\n    return [\n")

    with pytest.raises(WorkflowError, match="Cannot import LangGraph workflow"):
        start_workflow("broken", config=config)


def test_workflow_status_rejects_unknown_session(tmp_path: Path) -> None:
    config = HirakuConfig.init(tmp_path, example="workflows", install_skill=False)

    with pytest.raises(ValidationError, match="was not found"):
        workflow_status(f"unknown:{'0' * 32}", config=config)


def test_resume_rejects_stale_checkpoint(tmp_path: Path) -> None:
    config = HirakuConfig.init(tmp_path, example="workflows", install_skill=False)
    started = start_workflow("count-steps", {"count": 1}, config=config)

    with pytest.raises(ValidationError, match="checkpoint has changed"):
        resume_workflow(
            started["workflow_session_id"],
            {"decision": "finish"},
            config=config,
            checkpoint_id="stale",
        )
