from __future__ import annotations

import inspect
from concurrent.futures import ThreadPoolExecutor
from typing import Any

import anyio
import pytest
from click.testing import CliRunner
from mcp import Client, types
from mcp.server.elicitation import AcceptedElicitation
from mcp.server.mcpserver import Elicit

from hiraku.cli import main
from hiraku.errors import ValidationError
from hiraku.mcp import create_server
from hiraku.mcp import tools as record_tools
from hiraku.mcp.workflow_tools import (
    register_workflow_tools,
)
from hiraku.project import Project
from hiraku.record import Record, load_record
from hiraku.workflows.langgraph import resume_workflow as _resume_workflow
from hiraku.workflows.langgraph import (
    start_workflow,
    workflow_mermaid,
    workflow_status,
)


def resume_workflow(session_id, response, *, project):
    status = workflow_status(session_id, project=project)
    return _resume_workflow(
        session_id, response, project=project, checkpoint_id=status["checkpoint_id"]
    )


def _project(tmp_path) -> Project:
    return Project.create(tmp_path, example="workflows", install_skill=False)


def _tool_names(server: Any) -> set[str]:
    async def list_tools() -> set[str]:
        return {tool.name for tool in await server.list_tools()}

    return anyio.run(list_tools)


def _interrupt(result: dict[str, Any]) -> dict[str, Any]:
    assert result["status"] == "waiting"
    assert len(result["interrupts"]) == 1
    return result["interrupts"][0]["value"]


def _resume_resolver(server):
    tool = server._tool_manager.get_tool("workflow_resume")
    return tool.resolved_params["approval"][0].fn


def test_workflow_mcp_handlers_and_resolver_are_sync_sdk_handlers(tmp_path) -> None:
    server = create_server(_project(tmp_path))
    for name in (
        "workflow_start",
        "workflow_resume",
        "workflow_status",
        "workflow_sessions",
        "workflow_session_history",
    ):
        tool = server._tool_manager.get_tool(name)
        assert not inspect.iscoroutinefunction(tool.fn)
    resume = server._tool_manager.get_tool("workflow_resume")
    assert resume.resolver_plans[next(iter(resume.resolver_plans))].is_async is False


def test_workflow_registration_is_separate_from_record_tools(tmp_path) -> None:
    assert not hasattr(record_tools, "register_workflow_tools")
    assert callable(register_workflow_tools)
    assert {
        "workflow_list",
        "workflow_start",
        "workflow_sessions",
        "workflow_session_history",
        "workflow_status",
        "workflow_resume",
    } <= _tool_names(create_server(_project(tmp_path)))


def test_invalid_agent_response_can_retry_same_checkpoint(tmp_path) -> None:
    config = _project(tmp_path)
    Record.create("task", type="task", project=config)
    started = start_workflow(
        "collaborative",
        {"rounds": 1, "command": ["python", "train.py"]},
        project=config,
    )
    nested_response = {
        "draft": {
            "record_id": "draft-response",
            "summary": "Draft summary",
            "hypothesis": "A change improves the metric",
            "change": "Change one setting",
            "command": ["python", "train.py", "--setting", "1"],
        },
    }
    draft = {
        "record_id": "draft-response",
        "summary": "Draft summary",
        "hypothesis": "A change improves the metric",
        "change": "Change one setting",
        "command": ["python", "train.py", "--setting", "1"],
    }
    tool = create_server(config)._tool_manager.get_tool("workflow_resume")
    resolver = tool.resolved_params["approval"][0].fn
    with pytest.raises(
        ValidationError, match="Invalid response for 'draft_experiment'"
    ):
        resolved = resolver(
            started["workflow_session_id"], nested_response, started["checkpoint_id"]
        )
        tool.fn(
            started["workflow_session_id"],
            nested_response,
            started["checkpoint_id"],
            AcceptedElicitation[dict[str, Any]](data=resolved),
        )

    unchanged = workflow_status(started["workflow_session_id"], project=config)
    assert unchanged["checkpoint_id"] == started["checkpoint_id"]
    assert unchanged["status"] == "waiting"

    resolved = resolver(started["workflow_session_id"], draft, started["checkpoint_id"])
    result = tool.fn(
        started["workflow_session_id"],
        draft,
        started["checkpoint_id"],
        AcceptedElicitation[dict[str, Any]](data=resolved),
    )

    assert result["status"] == "waiting"
    assert result["checkpoint_id"] != started["checkpoint_id"]
    assert result["next_action"]["interaction"] == "approval"


def test_collaborative_start_uses_context_record_and_task_guidance(tmp_path) -> None:
    config = _project(tmp_path)
    Record.create("digits-classification", type="task", project=config)
    tool = create_server(config)._tool_manager.get_tool("workflow_start")
    started = tool.fn(
        "collaborative",
        {
            "task": "Improve validation performance while preserving the split.",
            "command": ["uv", "run", "python", "train.py", "--seed", "42"],
            "rounds": 3,
        },
        {"record_id": "digits-classification"},
    )

    pending = _interrupt(started)
    assert pending["data"]["task"] == "digits-classification"
    assert pending["data"]["objective"] == (
        "Improve validation performance while preserving the split."
    )


def test_invalid_resume_schema_does_not_advance_checkpoint(tmp_path) -> None:
    config = _project(tmp_path)
    started = start_workflow("count-steps", {}, project=config)
    pending = _interrupt(started)

    with pytest.raises(ValidationError, match="response_schema"):
        resume_workflow(
            started["workflow_session_id"],
            {"token": pending["data"]["token"], "extra": True},
            project=config,
        )

    current = workflow_status(started["workflow_session_id"], project=config)
    assert current["checkpoint_id"] == started["checkpoint_id"]
    assert current["status"] == "waiting"


def test_concurrent_resumes_advance_a_checkpoint_once(tmp_path) -> None:
    config = _project(tmp_path)
    started = start_workflow("count-steps", {}, project=config)
    pending = _interrupt(started)
    response = {"token": pending["data"]["token"], "decision": "finish"}

    def resume() -> dict[str, Any]:
        from hiraku.workflows.langgraph import resume_workflow as resume_graph

        return resume_graph(
            started["workflow_session_id"],
            response,
            project=config,
            checkpoint_id=started["checkpoint_id"],
        )

    with ThreadPoolExecutor(max_workers=2) as executor:
        futures = [executor.submit(resume) for _ in range(2)]
    results = []
    errors = []
    for future in futures:
        try:
            results.append(future.result())
        except ValidationError as exc:
            errors.append(exc)

    assert len(results) == 1
    assert results[0]["status"] == "completed"
    assert len(errors) == 1


def test_only_approval_interaction_returns_an_elicitation(tmp_path) -> None:
    config = Project.create(tmp_path, install_skill=False)
    Record.create("task", type="task", project=config)
    started = start_workflow(
        "collaborative", {"rounds": 1, "command": ["python"]}, project=config
    )
    resolver = _resume_resolver(create_server(config))
    draft_response = {
        "record_id": "resolver-draft",
        "summary": "Draft",
        "hypothesis": "It helps",
        "change": "Change one setting",
        "command": ["python", "train.py"],
    }
    assert (
        resolver(
            started["workflow_session_id"],
            draft_response,
            started["checkpoint_id"],
        )
        == draft_response
    )
    drafted = resume_workflow(
        started["workflow_session_id"], draft_response, project=config
    )
    assert _interrupt(drafted)["interaction"] == "approval"
    elicitation = resolver(started["workflow_session_id"], {}, drafted["checkpoint_id"])
    assert isinstance(elicitation, Elicit)


def test_human_review_is_conversational_and_schema_validated(tmp_path) -> None:
    config = Project.create(tmp_path, install_skill=False)
    directory = config.workflows_dir / "human-review"
    directory.mkdir()
    (directory / "workflow.md").write_text(
        "---\nid: human-review\ndescription: Review a proposal.\n---\n# Review\n",
        encoding="utf-8",
    )
    (directory / "workflow.py").write_text(
        "from typing import TypedDict\n"
        "from langgraph.graph import START, END, StateGraph\n"
        "from langgraph.types import interrupt\n"
        "class State(TypedDict, total=False):\n"
        "    final: str\n"
        "def input_schema():\n"
        "    return {\n"
        "        'type': 'object', 'properties': {}, 'additionalProperties': False\n"
        "    }\n"
        "def build_graph(checkpointer, *, project):\n"
        "    def review(state):\n"
        "        answer = interrupt({"
        "'actor': 'human', 'interaction': 'review', "
        "'response_schema': {'type': 'object', 'properties': {'answer': "
        "{'type': 'string', 'minLength': 1}}, 'required': ['answer'], "
        "'additionalProperties': False}})\n"
        "        return {'final': answer['answer']}\n"
        "    def prepare(state):\n"
        "        return {'final': ''}\n"
        "    graph = StateGraph(State)\n"
        "    graph.add_node('prepare', prepare)\n"
        "    graph.add_node('review', review)\n"
        "    graph.add_edge(START, 'prepare')\n"
        "    graph.add_edge('prepare', 'review')\n"
        "    graph.add_edge('review', END)\n"
        "    return graph.compile(checkpointer=checkpointer)\n",
        encoding="utf-8",
    )
    started = start_workflow("human-review", project=config)
    resolver = _resume_resolver(create_server(config))
    with pytest.raises(ValidationError, match="non-empty review response"):
        resolver(started["workflow_session_id"], {}, started["checkpoint_id"])
    with pytest.raises(ValidationError, match="response_schema"):
        resolver(
            started["workflow_session_id"],
            {"answer": ""},
            started["checkpoint_id"],
        )
    answer = {"answer": "Approved conversationally"}
    assert (
        resolver(started["workflow_session_id"], answer, started["checkpoint_id"])
        == answer
    )
    assert (
        workflow_status(started["workflow_session_id"], project=config)["checkpoint_id"]
        == started["checkpoint_id"]
    )


def test_revision_feedback_is_validated_before_checkpoint_resume(tmp_path) -> None:
    config = Project.create(tmp_path, install_skill=False)
    Record.create("task", type="task", project=config)
    started = start_workflow(
        "collaborative", {"rounds": 1, "command": ["python"]}, project=config
    )
    drafted = resume_workflow(
        started["workflow_session_id"],
        {
            "record_id": "revision-draft",
            "summary": "Draft",
            "hypothesis": "It helps",
            "change": "Change one setting",
            "command": ["python", "train.py"],
        },
        project=config,
    )
    pending = _interrupt(drafted)
    with pytest.raises(ValidationError, match="human workflow decision"):
        resume_workflow(
            started["workflow_session_id"],
            {"decision": "revise", "revision": pending["revision"]},
            project=config,
        )
    assert (
        workflow_status(started["workflow_session_id"], project=config)["checkpoint_id"]
        == drafted["checkpoint_id"]
    )
    assert load_record("revision-draft", project=config).status == "drafted"

    revised = resume_workflow(
        started["workflow_session_id"],
        {
            "decision": "revise",
            "revision": pending["revision"],
            "feedback": "Use a smaller change",
        },
        project=config,
    )
    assert _interrupt(revised)["action"] == "draft_experiment"
    assert _interrupt(revised)["data"]["feedback"] == "Use a smaller change"


def test_count_steps_mcp_tools_and_graph_loop(tmp_path) -> None:
    config = _project(tmp_path)
    server = create_server(config)
    assert {"workflow_start", "workflow_status", "workflow_resume"}.issubset(
        _tool_names(server)
    )

    started = start_workflow("count-steps", {"count": 2}, project=config)
    first = _interrupt(started)
    assert "Step 1/2: token" in first["instruction"]
    assert first["data"]["step"] == 1
    assert first["response_schema"]["type"] == "object"
    assert first["response_schema"]["additionalProperties"] is False
    assert first["response_schema"]["properties"]["decision"]["enum"] == [
        "continue",
        "finish",
    ]

    wrong = resume_workflow(
        started["workflow_session_id"],
        {"token": "wrong", "decision": "continue"},
        project=config,
    )
    retry = _interrupt(wrong)
    assert retry["data"] == first["data"]
    assert "does not match" in retry["error"]

    with pytest.raises(ValidationError, match="response_schema"):
        resume_workflow(
            started["workflow_session_id"],
            {"token": first["data"]["token"], "decision": "skip"},
            project=config,
        )
    assert (
        workflow_status(started["workflow_session_id"], project=config)["checkpoint_id"]
        == wrong["checkpoint_id"]
    )

    next_step = resume_workflow(
        started["workflow_session_id"],
        {"token": first["data"]["token"], "decision": "continue"},
        project=config,
    )
    second = _interrupt(next_step)
    assert "Step 2/2: token" in second["instruction"]
    assert second["data"]["token"] != first["data"]["token"]

    done = resume_workflow(
        started["workflow_session_id"],
        {"token": second["data"]["token"], "decision": "continue"},
        project=config,
    )
    assert done["status"] == "completed"
    assert "Finished after 2 of 2 steps." in done["result"]
    assert workflow_status(started["workflow_session_id"], project=config) == done


def test_count_steps_can_finish_early_and_resume_after_server_restart(tmp_path) -> None:
    config = _project(tmp_path)
    server = create_server(config)
    assert "workflow_resume" in _tool_names(server)

    started = start_workflow("count-steps", {}, project=config)
    first = _interrupt(started)
    assert first["data"]["count"] == 5

    # Recreate the server to verify that the run lives in LangGraph's SQLite state.
    server = create_server(config)
    assert "workflow_status" in _tool_names(server)
    completed = resume_workflow(
        started["workflow_session_id"],
        {"token": first["data"]["token"], "decision": "finish"},
        project=config,
    )
    assert completed["status"] == "completed"
    assert "Finished after 1 of 5 steps." in completed["result"]


def test_count_steps_rejects_invalid_arguments(tmp_path) -> None:
    config = _project(tmp_path)
    with pytest.raises(ValidationError, match="positive integer"):
        start_workflow("count-steps", {"count": 0}, project=config)


def test_mcp_exposes_only_the_langgraph_workflow_interface(tmp_path) -> None:
    config = _project(tmp_path)
    tools = _tool_names(create_server(config))
    assert {
        "workflow_list",
        "workflow_start",
        "workflow_status",
        "workflow_resume",
    } <= tools
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
        & tools
    )


def test_workflow_graph_is_available_from_cli_and_mcp(tmp_path, monkeypatch) -> None:
    config = _project(tmp_path)
    server = create_server(config)

    monkeypatch.chdir(tmp_path)
    cli_result = CliRunner().invoke(main, ["workflow", "graph", "count-steps"])
    assert cli_result.exit_code == 0, cli_result.output
    assert "graph TD" in cli_result.output
    assert "harness_step" in cli_result.output

    templates = anyio.run(server.list_resource_templates)
    assert "hiraku://workflows/{workflow_id}/graph" in {
        template.uri_template for template in templates
    }
    mermaid = workflow_mermaid("count-steps", project=config)
    assert "graph TD" in mermaid
    assert "harness_step" in mermaid


def test_collaborative_workflow_is_a_checkpointed_langgraph(tmp_path) -> None:
    config = Project.create(tmp_path, install_skill=False)
    Record.create(
        "task",
        type="task",
        project=config,
    )
    started = start_workflow(
        "collaborative",
        {"rounds": 1, "command": ["python", "train.py"]},
        project=config,
    )
    assert started["status"] == "waiting"
    interrupt = _interrupt(started)
    assert interrupt["action"] == "draft_experiment"
    assert interrupt["response_fields"] == [
        "record_id",
        "summary",
        "hypothesis",
        "change",
        "command",
    ]
    assert started["next_action"] == {
        "actor": "agent",
        "interaction": "task",
        "action": "draft_experiment",
        "requires_approval": False,
    }
    assert interrupt["data"]["task"] == "task"

    drafted = resume_workflow(
        started["workflow_session_id"],
        {
            "record_id": "draft-1",
            "summary": "Try a focused change",
            "hypothesis": "The change improves the metric",
            "change": "Apply one change",
            "command": ["python", "train.py"],
        },
        project=config,
    )
    approval_interrupt = _interrupt(drafted)
    assert approval_interrupt["actor"] == "human"
    assert approval_interrupt["interaction"] == "approval"
    assert drafted["next_action"]["requires_approval"] is True
    with pytest.raises(ValidationError, match="human workflow decision"):
        resume_workflow(
            started["workflow_session_id"],
            {"decision": []},
            project=config,
        )
    assert (
        workflow_status(started["workflow_session_id"], project=config)["checkpoint_id"]
        == drafted["checkpoint_id"]
    )
    completed = resume_workflow(
        started["workflow_session_id"],
        {"decision": "stop", "revision": approval_interrupt["revision"]},
        project=config,
    )
    assert completed["status"] == "completed"
    assert "remaining limitations" in completed["result"]


def test_collaborative_approval_uses_mcp_elicitation(tmp_path) -> None:
    config = Project.create(tmp_path, install_skill=False)
    Record.create(
        "task",
        type="task",
        project=config,
    )
    started = start_workflow(
        "collaborative",
        {"rounds": 1, "command": ["python", "train.py"]},
        project=config,
    )
    drafted = resume_workflow(
        started["workflow_session_id"],
        {
            "record_id": "draft-approval",
            "summary": "Draft a change",
            "hypothesis": "It helps",
            "change": "Change one thing",
            "command": ["python", "train.py"],
        },
        project=config,
    )
    approval_interrupt = _interrupt(drafted)
    assert "response_schema" not in approval_interrupt
    requested = []

    async def elicit(_context, params):
        requested.append(params)
        return types.ElicitResult(
            action="accept",
            content={"decision": "approve"},
        )

    async def exercise() -> Any:
        async with Client(
            create_server(config),
            elicitation_callback=elicit,
        ) as client:
            return await client.call_tool(
                "workflow_resume",
                {
                    "workflow_session_id": started["workflow_session_id"],
                    "response": {},
                    "checkpoint_id": drafted["checkpoint_id"],
                },
            )

    resumed = anyio.run(exercise)
    assert resumed.structured_content["status"] == "waiting"
    assert resumed.structured_content["next_action"]["actor"] == "agent"
    assert len(requested) == 1
    assert "Approve this experiment draft" in requested[0].message
    assert "Change one thing" in requested[0].message
    schema = requested[0].requested_schema
    assert set(schema) <= {"type", "properties", "required", "$schema"}
    assert schema["properties"]["decision"]["enum"] == [
        "approve",
        "revise",
        "stop",
    ]
    assert set(schema["properties"]) == {"decision", "feedback"}
    assert approval_interrupt["revision"]


def test_agent_handoff_resumes_without_elicitation(tmp_path) -> None:
    config = Project.create(tmp_path, install_skill=False)
    Record.create("task", type="task", project=config)
    started = start_workflow(
        "collaborative",
        {"rounds": 1, "command": ["python", "train.py"]},
        project=config,
    )

    async def exercise() -> Any:
        async with Client(create_server(config)) as client:
            return await client.call_tool(
                "workflow_resume",
                {
                    "workflow_session_id": started["workflow_session_id"],
                    "checkpoint_id": started["checkpoint_id"],
                    "response": {
                        "record_id": "agent-draft",
                        "summary": "Draft summary",
                        "hypothesis": "A change improves the metric",
                        "change": "Change one setting",
                        "command": ["python", "train.py", "--setting", "1"],
                    },
                },
            )

    resumed = anyio.run(exercise)
    assert resumed.structured_content["status"] == "waiting"
    assert resumed.structured_content["next_action"] == {
        "actor": "human",
        "interaction": "approval",
        "action": "approve_experiment_draft",
        "requires_approval": True,
    }


def test_cancelled_collaborative_approval_leaves_run_waiting(tmp_path) -> None:
    config = Project.create(tmp_path, install_skill=False)
    Record.create(
        "task",
        type="task",
        project=config,
    )
    started = start_workflow(
        "collaborative",
        {"rounds": 1, "command": ["python", "train.py"]},
        project=config,
    )
    drafted = resume_workflow(
        started["workflow_session_id"],
        {
            "record_id": "draft-cancel",
            "summary": "Draft",
            "hypothesis": "Hypothesis",
            "change": "Change",
            "command": ["python", "train.py"],
        },
        project=config,
    )

    async def elicit(_context, _params):
        return types.ElicitResult(action="cancel")

    async def exercise() -> tuple[Any, Any]:
        async with Client(
            create_server(config),
            elicitation_callback=elicit,
        ) as client:
            approval = await client.call_tool(
                "workflow_resume",
                {
                    "workflow_session_id": started["workflow_session_id"],
                    "response": {},
                    "checkpoint_id": drafted["checkpoint_id"],
                },
            )
            status = await client.call_tool(
                "workflow_status",
                {"workflow_session_id": started["workflow_session_id"]},
            )
            return approval, status

    approval, status = anyio.run(exercise)
    assert approval.structured_content["decision"] == "cancel"
    assert status.structured_content["status"] == "waiting"


def test_mcp_approval_supports_other_workflows(tmp_path) -> None:
    config = Project.create(tmp_path, install_skill=False)
    directory = config.workflows_dir / "review"
    directory.mkdir()
    (directory / "workflow.md").write_text(
        "---\nid: review\ndescription: Review task.\n---\n# Review\n",
        encoding="utf-8",
    )
    (directory / "workflow.py").write_text(
        "from langgraph.graph import START, END, StateGraph\n"
        "from langgraph.types import interrupt\n"
        "from typing import TypedDict\n"
        "class State(TypedDict, total=False):\n"
        "    run_id: str\n"
        "    final: str\n"
        "def input_schema():\n"
        "    return {\n"
        "        'type': 'object',\n"
        "        'properties': {},\n"
        "        'additionalProperties': False,\n"
        "    }\n"
        "def build_graph(checkpointer, *, project):\n"
        "    def review(state):\n"
        "        answer = interrupt({'approval': {'message': 'Review this'}})\n"
        "        return {'final': answer['response']['value']}\n"
        "    graph = StateGraph(State)\n"
        "    graph.add_node('review', review)\n"
        "    graph.add_edge(START, 'review')\n"
        "    graph.add_edge('review', END)\n"
        "    return graph.compile(checkpointer=checkpointer)\n",
        encoding="utf-8",
    )
    started = start_workflow("review", project=config)

    async def elicit(_context, _params):
        return types.ElicitResult(action="accept", content={"decision": "approve"})

    async def exercise():
        async with Client(create_server(config), elicitation_callback=elicit) as client:
            return await client.call_tool(
                "workflow_resume",
                {
                    "workflow_session_id": started["workflow_session_id"],
                    "response": {"value": "approved"},
                    "checkpoint_id": started["checkpoint_id"],
                },
            )

    result = anyio.run(exercise)
    assert result.structured_content["result"] == "approved"


def test_mcp_resume_elicits_approval_for_checkpointed_workflow(tmp_path) -> None:
    config = Project.create(tmp_path, install_skill=False)
    Record.create(
        "task",
        type="task",
        project=config,
    )
    first = start_workflow(
        "collaborative",
        {"rounds": 1, "command": ["python", "train.py"]},
        project=config,
    )
    first = resume_workflow(
        first["workflow_session_id"],
        {
            "record_id": "draft-final",
            "summary": "Draft",
            "hypothesis": "Hypothesis",
            "change": "Change",
            "command": ["python", "train.py"],
        },
        project=config,
    )

    async def elicit(_context, _params):
        return types.ElicitResult(action="accept", content={"decision": "stop"})

    async def exercise():
        async with Client(create_server(config), elicitation_callback=elicit) as client:
            return await client.call_tool(
                "workflow_resume",
                {
                    "workflow_session_id": first["workflow_session_id"],
                    "response": {},
                    "checkpoint_id": first["checkpoint_id"],
                },
            )

    resumed = anyio.run(exercise)
    assert resumed.structured_content["status"] == "completed"
