from __future__ import annotations

import asyncio

from hiraku.mcp import create_server
from hiraku.project import Project


def test_workflow_mcp_uses_generic_langgraph_controls(tmp_path) -> None:
    config = Project.create(tmp_path, example="workflows", install_skill=False)
    server = create_server(config)

    tools = asyncio.run(server.list_tools())
    names = {tool.name for tool in tools}
    assert {
        "workflow_list",
        "workflow_start",
        "workflow_status",
        "workflow_resume",
        "workflow_approval",
    } <= names
    assert (
        not {
            "workflow_send_event",
            "session_list",
            "session_show",
            "session_cancel",
            "count-steps",
            "greet",
            "scheduler",
        }
        & names
    )
