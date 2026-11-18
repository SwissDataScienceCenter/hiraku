from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path
from typing import Any
from urllib.parse import quote

import anyio
import psij
import pytest
from click.testing import CliRunner

pytest.importorskip("mcp")

from mcp import (  # noqa: E402
    Client,
    ClientSession,
    StdioServerParameters,
    types,
)
from mcp.client.stdio import stdio_client  # noqa: E402
from mcp.server.elicitation import render_elicitation_schema  # noqa: E402
from mcp.server.mcpserver.exceptions import ResourceError, ToolError  # noqa: E402

from hiraku.cli import main  # noqa: E402
from hiraku.errors import ObservationError, ValidationError  # noqa: E402
from hiraku.job import Submission  # noqa: E402
from hiraku.mcp import (  # noqa: E402
    WorkflowApproval,
    create_server,
    register_tools,
    register_workflow_tools,
)
from hiraku.project import Project  # noqa: E402
from hiraku.workflows.langgraph import resume_workflow as _resume_workflow  # noqa: E402
from hiraku.workflows.langgraph import (  # noqa: E402
    start_workflow,
    workflow_mermaid,
    workflow_status,
)


def test_registration_exposes_described_tools(tmp_path: Path, monkeypatch) -> None:
    from mcp.server.mcpserver import MCPServer

    project = Project.create(tmp_path, install_skill=False)
    server = MCPServer("test")
    register_tools(server, project)
    register_workflow_tools(server, project)

    async def run_inline(func, *args, **kwargs):
        return func(*args)

    monkeypatch.setattr(anyio.to_thread, "run_sync", run_inline)

    async def exercise() -> None:
        tools = {tool.name: tool for tool in await server.list_tools()}
        assert {
            "project_info",
            "leaderboard_list",
            "record_list",
            "runs",
            "workflow_start",
        } <= tools.keys()
        assert {"job_submit", "job_status", "job_list"} <= tools.keys()
        assert tools["project_info"].title == "Project information"
        assert tools["record_list"].annotations.read_only_hint is True
        assert tools["job_status"].annotations.read_only_hint is False
        assert {"job_show", "job_sync"}.isdisjoint(tools)
        assert tools["job_list"].annotations.read_only_hint is False
        assert tools["job_list"].annotations.open_world_hint is True
        assert tools["workflow_start"].title == "Start workflow"
        assert tools["workflow_start"].description
        record_schema = tools["record_create"].input_schema
        create_input = record_schema["$defs"]["Record"]
        assert {"type", "id"} <= set(create_input["required"])
        assert create_input["properties"]["type"]["description"]
        update_input = tools["record_update"].input_schema["$defs"]["RecordUpdate"]
        assert update_input["properties"]["status"]["description"]
        assert "unset" not in update_input["properties"]
        job_input = tools["job_submit"].input_schema["$defs"]["JobRequest"]
        assert job_input["additionalProperties"] is False
        assert job_input["properties"]["command"]["description"]
        result = await server.call_tool("project_info", {})
        assert result.structured_content == project.to_dict()

    asyncio.run(exercise())


def test_tools_and_validation(tmp_path: Path, monkeypatch) -> None:
    config = Project.create(tmp_path)
    server = create_server(config)

    async def run_inline(func, *args, **kwargs):
        return func(*args)

    monkeypatch.setattr(anyio.to_thread, "run_sync", run_inline)

    async def exercise() -> None:
        async def call(tool_name: str, **args):
            return await server.call_tool(tool_name, args)

        await call(
            "record_create",
            record={
                "type": "task",
                "title": "root",
                "id": "root/root",
                "state": "new",
            },
        )
        await call(
            "record_create",
            record={
                "type": "task",
                "title": "child",
                "parent_id": "root/root",
                "id": "root/child/child",
            },
        )
        await call(
            "record_update",
            record_id="root/child/child",
            patch={"state": "done"},
        )
        assert config.vault.load("root/child/child").state == "done"
        for name, args in [
            (
                "record_create",
                {
                    "record": {
                        "id": "../bad/bad",
                        "type": "task",
                        "title": "bad",
                        "parent_id": "../missing",
                    }
                },
            ),
            (
                "record_create",
                {"record": {"id": "  ", "type": "task", "title": "  "}},
            ),
            (
                "record_create",
                {
                    "record": {
                        "id": "root/my run",
                        "type": "task",
                        "title": "bad",
                    }
                },
            ),
            (
                "record_update",
                {
                    "record_id": "root/child/child",
                    "patch": {"id": "renamed"},
                },
            ),
            (
                "record_update",
                {
                    "record_id": "root/child/child",
                    "patch": {"related_to": ["../no"]},
                },
            ),
            ("record_delete", {"record_id": "missing"}),
        ]:
            with pytest.raises(ToolError):
                await server.call_tool(name, args)
        assert config.vault.load("root/child/child").related_to == []
        await call(
            "record_create",
            record={
                "id": "root/unknown",
                "type": "task",
                "extra_field": "producer data",
            },
        )
        assert config.vault.load("root/unknown").extra_field == "producer data"
        await call(
            "record_update",
            record_id="root/child/child",
            patch={"unknown_field": "producer update"},
        )
        assert config.vault.load("root/child/child").unknown_field == "producer update"
        await call("record_delete", record_id="root/child/child")
        await call(
            "record_create",
            record={
                "type": "context",
                "title": "sources",
                "markdown": "Evidence",
                "id": "sources/sources",
            },
        )
        with pytest.raises(ToolError):
            await call(
                "record_create",
                record={"type": "task", "title": "  ", "id": "  "},
            )
        await call(
            "record_update",
            record_id="sources/sources",
            patch={"markdown": "Updated"},
        )
        assert config.vault.load("sources/sources").markdown == "Updated"
        await call("record_show", record_id="sources/sources")
        await call("record_list")
        await call("record_show", record_id="root/root")
        await call("record_list")
        await call("record_delete", record_id="sources/sources")
        assert "sources" not in {record.title for record in config.vault.list()}

    asyncio.run(exercise())


@pytest.mark.parametrize("deleted_id", ["active", "other"])
def test_record_delete_does_not_manage_project_state(tmp_path, deleted_id, monkeypatch):
    project = Project.create(tmp_path, install_skill=False, install_mcp=False)
    project.vault.create("active", type="task")
    project.vault.create("other", type="task")
    server = create_server(project)

    async def run_inline(func, *args, **kwargs):
        return func(*args)

    monkeypatch.setattr(anyio.to_thread, "run_sync", run_inline)

    asyncio.run(server.call_tool("record_delete", {"record_id": deleted_id}))

    assert not project.vault.file_path(deleted_id).exists()


def test_record_update_uses_vault_validation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    project = Project.create(tmp_path, install_skill=False)
    record = project.vault.create(
        "original/original",
        type="task",
        state="new",
        markdown="Before",
        title="original",
    )
    other = project.vault.create("other/other", type="task", title="other")
    server = create_server(project)

    # Exercise MCP routing without relying on the SDK's worker-thread scheduler.
    async def run_inline(func, *args, **kwargs):
        return func(*args)

    monkeypatch.setattr(anyio.to_thread, "run_sync", run_inline)

    async def exercise() -> None:
        result = await server.call_tool(
            "record_update",
            {
                "record_id": record.id,
                "patch": {"related_to": [other.id], "markdown": "After"},
            },
        )
        assert result.structured_content["title"] == "original"
        updated = project.vault.load(record.id)
        assert updated.id == "original/original"
        assert updated.state == "new"
        assert updated.markdown == "After"
        assert updated.related_to == [other.id]

        for patch in ({"related_to": ["../missing"]}, {"title": []}):
            with pytest.raises(ToolError):
                await server.call_tool(
                    "record_update", {"record_id": record.id, "patch": patch}
                )
        result = await server.call_tool(
            "record_update", {"record_id": record.id, "patch": {"title": None}}
        )
        assert result.structured_content["title"] is None
        assert project.vault.load(record.id).title is None
        assert project.vault.load(record.id).related_to == [other.id]

    asyncio.run(exercise())


def test_resources_and_prompt(tmp_path: Path, monkeypatch) -> None:
    config = Project.create(tmp_path)
    server = create_server(config)

    async def run_inline(func, *args, **kwargs):
        return func(*args)

    monkeypatch.setattr(anyio.to_thread, "run_sync", run_inline)

    async def exercise() -> None:
        await server.call_tool(
            "record_create",
            {"record": {"id": "demo/demo", "type": "task", "title": "demo"}},
        )
        await server.call_tool(
            "record_create",
            {
                "record": {
                    "id": "literature/literature",
                    "type": "context",
                    "title": "literature",
                    "markdown": "Curated evidence",
                }
            },
        )
        literature = config.vault.load("literature/literature")
        catalog = list(await server.read_resource("hiraku://catalog"))[0]
        assert json.loads(catalog.content) == ["demo/demo", "literature/literature"]
        record_uri = f"hiraku://records/{quote(literature.id, safe='')}"
        assert list(await server.read_resource(record_uri))[0].content
        for uri in [
            "hiraku://records/demo%2Fdemo",
            "hiraku://records/literature%2Fliterature",
        ]:
            assert list(await server.read_resource(uri))[0].content
        with pytest.raises(ResourceError, match="'missing' not found"):
            await server.read_resource("hiraku://records/missing")
        prompt = await server.get_prompt(
            "propose_implementation",
            {
                "record_id": "demo/demo",
                "related_record_ids": "literature/literature",
            },
        )
        assert "Curated evidence" in prompt.messages[0].content.text

    asyncio.run(exercise())


def test_submit_rejects_native_local(tmp_path: Path, monkeypatch) -> None:
    server = create_server(Project.create(tmp_path))
    monkeypatch.setattr(
        "psij.executors.local.LocalJobExecutor.submit",
        lambda *args: pytest.fail("Native local job must not be submitted"),
    )

    async def run_inline(func, *args, **kwargs):
        return func(*args)

    monkeypatch.setattr(anyio.to_thread, "run_sync", run_inline)

    async def exercise() -> None:
        with pytest.raises(ToolError):
            await server.call_tool(
                "job_submit",
                {"request": {"command": ["echo", "hello"], "executorr": "local"}},
            )
        with pytest.raises(ToolError, match="use hiraku run or the local-detached"):
            await server.call_tool(
                "job_submit",
                {"request": {"command": ["echo", "hello"], "executor": "local"}},
            )

    asyncio.run(exercise())
    assert not (tmp_path / ".hiraku/jobs").exists()


@pytest.mark.parametrize("state", ["ACTIVE", "COMPLETED", "FAILED", "CANCELED"])
@pytest.mark.parametrize("unavailable", [False, True])
def test_job_list_refreshes_only_nonfinal_jobs(
    tmp_path, monkeypatch, caplog, state, unavailable
):
    project = Project.create(tmp_path)
    job = psij.Job(psij.JobSpec("echo"))
    job.executor = psij.JobExecutor.get_instance(
        "local-detached", url=(tmp_path / "backend").as_uri()
    )
    job._native_id = job.id
    job.status = psij.JobStatus(getattr(psij.JobState, state))
    entry = Submission.from_psi_job(job)
    entry.save(project)
    path = project.root / ".hiraku/jobs" / f"{job.id}.json"
    before = path.read_bytes()
    synced = []

    def sync(self):
        synced.append(self.id)
        if unavailable:
            raise ObservationError("backend unavailable")
        self.status.state = "COMPLETED"

    monkeypatch.setattr(Submission, "sync", sync)
    server = create_server(project)

    async def run_inline(func, *args, **kwargs):
        return func(*args)

    monkeypatch.setattr(anyio.to_thread, "run_sync", run_inline)

    async def exercise():
        result = await server.call_tool("job_list", {})
        expected = "COMPLETED" if state == "ACTIVE" and not unavailable else state
        listed = result.structured_content["result"][0]
        if state == "ACTIVE" and unavailable:
            assert listed["status"]["state"] == "UNKNOWN"
            assert listed["observation_error"] == "backend unavailable"
            assert listed["observed_at"] == entry.model_dump(mode="json")["observed_at"]
            assert path.read_bytes() == before
        else:
            assert listed["status"]["state"] == expected
            assert "observation_error" not in listed
        assert Submission.load(project, job.id).status.state == expected

    asyncio.run(exercise())
    assert synced == ([job.id] if state == "ACTIVE" else [])
    if state == "ACTIVE" and unavailable:
        assert job.id in caplog.text and "backend unavailable" in caplog.text


@pytest.mark.parametrize("custom_url", [False, True])
def test_submit_and_status_use_native_executor(
    tmp_path: Path, monkeypatch, custom_url
) -> None:
    config = Project.create(tmp_path)
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("HIRAKU_MCP_TEST_HOST_ENV", "forwarded")
    server = create_server(config)
    config.vault.create("active", title="Active", type="task")

    async def run_inline(func, *args, **kwargs):
        return func(*args)

    monkeypatch.setattr(anyio.to_thread, "run_sync", run_inline)

    async def exercise() -> None:
        async def completed_job(receipt):
            for _ in range(300):
                result = await server.call_tool(
                    "job_status",
                    {"job_id": receipt["id"][:8]},
                )
                if result.structured_content["status"]["state"] in {
                    "COMPLETED",
                    "FAILED",
                    "CANCELED",
                }:
                    return result
                await asyncio.sleep(0.05)
            pytest.fail("Submitted job did not finish")

        await server.call_tool(
            "record_create",
            {"record": {"id": "demo/demo", "type": "task", "title": "demo"}},
        )
        result = await server.call_tool(
            "job_submit",
            {
                "request": {
                    "record_id": "demo/demo",
                    **(
                        {"executor_url": (tmp_path / "backend").as_uri()}
                        if custom_url
                        else {}
                    ),
                    "launcher": "single",
                    "custom_attributes": {"example": "value"},
                    "command": [
                        sys.executable,
                        "-c",
                        "import sys,time; from pathlib import Path\n"
                        f"while not Path({str(config.root / 'release')!r}).exists(): "
                        "time.sleep(.05)\n"
                        "import os; print(os.environ['HIRAKU_RECORD_ID']); "
                        "print('err', file=sys.stderr); "
                        "raise SystemExit(7)",
                    ],
                }
            },
        )
        job = dict(result.structured_content)
        assert "observation_error" not in job
        assert job["executor"] == "local-detached"
        expected = (
            tmp_path / "backend" if custom_url else tmp_path / ".hiraku/psij/detached"
        )
        assert job["executor_url"] == expected.as_uri()
        assert list((tmp_path / ".hiraku/psij").glob("*/single_launch.sh"))
        assert job["spec"]["launcher"] == "single"
        assert job["spec"]["environment"]["HIRAKU_MCP_TEST_HOST_ENV"] == "forwarded"
        assert job["status"]["state"] in {"ACTIVE", "QUEUED"}
        (config.root / "release").touch()
        completed = await completed_job(job)
        assert completed.structured_content["status"]["exit_code"] == 7
        output = expected / job["native_id"]
        assert (output / "stdout.log").read_text() == "demo/demo\n"
        assert (output / "stderr.log").read_text() == "err\n"
        history = json.loads(
            (config.root / ".hiraku/jobs" / f"{job['id']}.json").read_text()
        )
        assert history["status"]["exit_code"] == 7
        assert history["id"] == job["id"]
        assert history["spec"]["launcher"] == "single"
        assert history["spec"]["attributes"]["custom_attributes"] == {
            "example": "value"
        }
        listed = await server.call_tool("job_list", {"record_id": "demo/demo"})
        assert listed.structured_content["result"] == [history]
        cli = CliRunner().invoke(
            main, ["job", "list", "--record", "demo/demo", "--json"]
        )
        assert cli.exit_code == 0, cli.output
        assert json.loads(cli.output) == listed.structured_content["result"]
        with pytest.raises(ToolError):
            await server.call_tool(
                "job_submit",
                {
                    "request": {
                        "command": [sys.executable, "-c", "pass"],
                        "launcher": "not-installed",
                        "executor_url": (tmp_path / "backend").as_uri(),
                    }
                },
            )
        submitted = await server.call_tool(
            "job_submit",
            {
                "request": {
                    "executor": "local-detached",
                    "executor_url": (tmp_path / "backend").as_uri(),
                    "record_id": "demo/demo",
                    "command": [sys.executable, "-c", "raise SystemExit(3)"],
                }
            },
        )
        assert submitted.structured_content["executor"] == "local-detached"
        completed = await completed_job(submitted.structured_content)
        assert completed.structured_content["status"]["exit_code"] == 3
        submitted = await server.call_tool(
            "job_submit",
            {
                "request": {
                    "command": [sys.executable, "-c", "pass"],
                    "executor_url": (tmp_path / "backend").as_uri(),
                }
            },
        )
        completed = await completed_job(submitted.structured_content)
        assert completed.structured_content["status"]["exit_code"] == 0
        assert completed.structured_content["record_id"] is None
        assert (
            completed.structured_content["spec"]["environment"]["HIRAKU_RECORD_ID"]
            == ""
        )

        def unavailable(self):
            raise ObservationError("backend unavailable")

        cached = completed.structured_content
        path = config.root / ".hiraku/jobs" / f"{cached['id']}.json"
        before = path.read_bytes()
        monkeypatch.setattr("hiraku.job.Submission.get_psi_job", unavailable)
        with pytest.raises(ToolError, match="backend unavailable"):
            await server.call_tool("job_status", {"job_id": cached["id"]})
        assert path.read_bytes() == before
        listed = await server.call_tool("job_list", {})
        assert json.loads(before) in listed.structured_content["result"]
        assert all(
            "observation_error" not in entry
            for entry in listed.structured_content["result"]
        )
        assert path.read_bytes() == before

    asyncio.run(exercise())


@pytest.mark.parametrize(
    "error",
    [
        psij.InvalidJobException("Set the renku.launcher attribute"),
        psij.SubmitException("rnk unavailable"),
        ValueError("Unknown executor"),
        FileNotFoundError("rnk executable missing"),
    ],
)
@pytest.mark.parametrize("during_submit", [False, True])
def test_submit_exposes_expected_backend_errors(
    tmp_path, monkeypatch, error, during_submit
):
    server = create_server(Project.create(tmp_path))

    async def run_inline(func, *args, **kwargs):
        return func(*args)

    monkeypatch.setattr(anyio.to_thread, "run_sync", run_inline)

    def fail(*args, **kwargs):
        raise error

    if during_submit:
        from types import SimpleNamespace

        monkeypatch.setattr(
            "hiraku.mcp.get_executor",
            lambda *args, **kwargs: SimpleNamespace(submit=fail),
        )
    else:
        monkeypatch.setattr("hiraku.mcp.get_executor", fail)

    async def exercise():
        with pytest.raises(ToolError) as caught:
            await server.call_tool(
                "job_submit", {"request": {"command": ["echo", "hello"]}}
            )
        assert str(error) in str(caught.value)

    asyncio.run(exercise())
    assert not (tmp_path / ".hiraku/jobs").exists()


def test_leaderboard_list_matches_cli_and_reads_current_definitions(
    tmp_path, monkeypatch
):
    project = Project.create(tmp_path, install_skill=False, install_mcp=False)
    monkeypatch.chdir(tmp_path)
    server = create_server(project)

    async def run_inline(func, *args, **kwargs):
        return func(*args)

    monkeypatch.setattr(anyio.to_thread, "run_sync", run_inline)

    async def exercise():
        empty = await server.call_tool("leaderboard_list", {})
        assert empty.structured_content["result"] == []
        (project.leaderboards_dir / "local.yml").write_text(
            "source: {type: csv, path: runs.csv}\n"
        )
        (project.leaderboards_dir / "broken.yml").write_text("source: [invalid]\n")
        result = await server.call_tool("leaderboard_list", {})
        cli = CliRunner().invoke(main, ["leaderboard", "list", "--json"])
        assert cli.exit_code == 0, cli.output
        rows = result.structured_content["result"]
        assert rows == json.loads(cli.output)
        assert rows == ["broken", "local"]
        info = await server.call_tool("project_info", {})
        assert info.structured_content == project.to_dict()

    asyncio.run(exercise())


def test_runs(tmp_path: Path, monkeypatch) -> None:
    config = Project.create(tmp_path)
    (config.leaderboards_dir / "default.yml").write_text(
        "source: {type: csv, path: runs.csv}\n"
        "metrics: [{name: acc, direction: maximize}]\n"
    )
    (tmp_path / "runs.csv").write_text(
        "id,record_id,metric.acc\na,task/task,0.5\nb,task/task,0.9\nc,other,0.7\nd,,1\n"
    )
    server = create_server(config)

    async def run_inline(func, *args, **kwargs):
        return func(*args)

    monkeypatch.setattr(anyio.to_thread, "run_sync", run_inline)

    async def exercise() -> None:
        await server.call_tool(
            "record_create",
            {"record": {"id": "task/task", "type": "task", "title": "task"}},
        )

        async def ids(**args) -> list[str]:
            result = await server.call_tool("runs", {"record_id": "task/task", **args})
            return [run["id"] for run in result.structured_content["result"]]

        assert await ids(sort="acc") == ["b", "a"]
        assert await ids(sort="acc", ascending=True, limit=1) == ["a"]
        assert sorted(await ids(record_id=None)) == ["a", "b", "c"]
        assert await ids(
            record_id=None, include_unattributed=True, sort="acc", limit=1
        ) == ["d"]
        assert await ids(record_id=None, sort="acc", limit=1) == ["b"]
        assert await ids(include_unattributed=True, sort="acc", limit=1) == ["b"]
        with pytest.raises(ToolError, match="requires a sort"):
            await server.call_tool(
                "runs", {"record_id": "task/task", "ascending": True}
            )

    asyncio.run(exercise())


def test_stdio_session(tmp_path: Path) -> None:
    Project.create(tmp_path)

    async def exercise() -> None:
        params = StdioServerParameters(
            command=sys.executable,
            args=[
                "-c",
                'from hiraku.cli import main; main(["mcp", "serve"])',
            ],
            cwd=str(tmp_path),
        )
        async with stdio_client(params) as (read, write):  # noqa: SIM117
            async with ClientSession(read, write, read_timeout_seconds=30) as client:
                await client.initialize()
                tools = await client.list_tools()
                assert "record_create" in {tool.name for tool in tools.tools}
                assert (
                    str((await client.list_resources()).resources[0].uri)
                    == "hiraku://catalog"
                )
                assert (
                    len((await client.list_resource_templates()).resource_templates)
                    == 3
                )
                assert (await client.list_prompts()).prompts[
                    0
                ].name == "propose_implementation"
                assert not (
                    await client.call_tool(
                        "record_create",
                        {
                            "record": {
                                "id": "papers/papers",
                                "type": "context",
                                "title": "papers",
                                "markdown": "Literature",
                            }
                        },
                    )
                ).is_error
                result = await client.read_resource("hiraku://catalog")
                assert json.loads(result.contents[0].text) == ["papers/papers"]
                result = await client.read_resource("hiraku://records/papers%2Fpapers")
                assert json.loads(result.contents[0].text)["id"] == "papers/papers"
                error = await client.call_tool("record_show", {"record_id": "missing"})
                assert error.is_error
                assert "missing" in error.content[0].text

    asyncio.run(exercise())


@pytest.mark.parametrize("args", [("mcp", "serve")])
def test_cli_project_error(tmp_path: Path, monkeypatch, args) -> None:
    monkeypatch.chdir(tmp_path)
    result = CliRunner().invoke(main, list(args))
    assert result.exit_code == 1
    assert "No hiraku.yml" in result.output


@pytest.mark.parametrize("args", [("mcp", "serve")])
def test_cli_serves_project(tmp_path, monkeypatch, args):
    from unittest.mock import Mock

    config = Project.create(tmp_path, install_skill=False)
    monkeypatch.chdir(tmp_path)
    factory = Mock()
    monkeypatch.setattr("hiraku.mcp.create_server", factory)
    result = CliRunner().invoke(main, list(args))
    assert result.exit_code == 0, result.output
    assert factory.call_args.kwargs == {}
    assert factory.call_args.args[0].path == config.path
    factory.return_value.run.assert_called_once_with(transport="stdio")


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


def resume_workflow(session_id, response, *, project):
    status = workflow_status(session_id, project=project)
    return _resume_workflow(
        session_id, response, project=project, checkpoint_id=status["checkpoint_id"]
    )


def _project(tmp_path) -> Project:
    return Project.create(tmp_path, example="workflows", install_skill=False)


def _tool_names(server: Any) -> set[str]:
    return {tool.name for tool in asyncio.run(server.list_tools())}


def _interrupt(result: dict[str, Any]) -> dict[str, Any]:
    assert result["status"] == "waiting"
    assert len(result["interrupts"]) == 1
    return result["interrupts"][0]["value"]


def test_workflow_approval_schema_omits_codex_unsupported_root_title() -> None:
    schema = render_elicitation_schema(WorkflowApproval)
    assert set(schema) <= {"type", "properties", "required", "$schema"}
    assert schema["properties"]["decision"]["enum"] == [
        "approve",
        "revise",
        "stop",
    ]
    assert set(schema["properties"]) == {"decision", "feedback"}


def test_count_steps_mcp_tools_and_graph_loop(tmp_path) -> None:
    config = _project(tmp_path)
    server = create_server(config)
    assert {"workflow_start", "workflow_status", "workflow_resume"}.issubset(
        _tool_names(server)
    )
    assert "workflow_approval" in _tool_names(server)

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

    invalid_decision = resume_workflow(
        started["workflow_session_id"],
        {"token": first["data"]["token"], "decision": "skip"},
        project=config,
    )
    retry = _interrupt(invalid_decision)
    assert retry["data"] == first["data"]
    assert "continue" in retry["error"] and "finish" in retry["error"]

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
    config.vault.create("task/task", type="task", title="task")
    started = start_workflow(
        "collaborative",
        {"task": "task/task", "rounds": 1, "command": ["python", "train.py"]},
        project=config,
    )
    assert started["status"] == "waiting"
    interrupt = _interrupt(started)
    assert interrupt["step"] == "draft"
    assert interrupt["data"]["task"] == config.vault.load("task/task").id

    bypass = resume_workflow(
        started["workflow_session_id"], {"event": "finish"}, project=config
    )
    assert bypass["status"] == "waiting"
    assert "approval decision is required" in _interrupt(bypass)["error"]
    invalid = resume_workflow(
        started["workflow_session_id"], {"approval": {"decision": []}}, project=config
    )
    assert "approval decision is required" in _interrupt(invalid)["error"]
    revised = resume_workflow(
        started["workflow_session_id"],
        {"approval": {"decision": "revise", "feedback": "Use a smaller model."}},
        project=config,
    )
    assert _interrupt(revised)["feedback"] == "Use a smaller model."
    completed = resume_workflow(
        started["workflow_session_id"],
        {"approval": {"decision": "stop"}},
        project=config,
    )
    assert completed["status"] == "completed"
    assert "results" in completed["result"]


def test_collaborative_approval_uses_mcp_elicitation(tmp_path) -> None:
    config = Project.create(tmp_path, install_skill=False)
    config.vault.create("task/task", type="task", title="task")
    started = start_workflow(
        "collaborative",
        {"task": "task/task", "rounds": 1, "command": ["python", "train.py"]},
        project=config,
    )
    requested = []

    async def elicit(_context, params):
        requested.append(params)
        return types.ElicitResult(
            action="accept",
            content={"decision": "approve"},
        )

    async def exercise() -> tuple[Any, Any]:
        async with Client(
            create_server(config),
            elicitation_callback=elicit,
        ) as client:
            approval = await client.call_tool(
                "workflow_approval",
                {
                    "workflow_session_id": started["workflow_session_id"],
                    "interrupt_id": started["interrupts"][0]["id"],
                    "proposal": "Try a larger learning rate.",
                    "response": {"event": "finish"},
                },
            )
            resumed = await client.call_tool(
                "workflow_resume",
                {
                    "request": {
                        "workflow_session_id": started["workflow_session_id"],
                        "response": approval.structured_content,
                        "checkpoint_id": started["checkpoint_id"],
                    }
                },
            )
            return approval, resumed

    result, resumed = anyio.run(exercise)
    assert resumed.structured_content["status"] == "completed"
    assert len(requested) == 1
    assert "draft" in requested[0].message
    assert "larger learning rate" in requested[0].message
    schema = requested[0].requested_schema
    assert set(schema) <= {"type", "properties", "required", "$schema"}
    assert schema["properties"]["decision"]["enum"] == [
        "approve",
        "revise",
        "stop",
    ]
    assert set(schema["properties"]) == {"decision", "feedback"}
    assert result.structured_content == {
        "approval": {"decision": "approve"},
        "response": {"event": "finish"},
    }


def test_cancelled_collaborative_approval_leaves_run_waiting(tmp_path) -> None:
    config = Project.create(tmp_path, install_skill=False)
    config.vault.create("task/task", type="task", title="task")
    started = start_workflow(
        "collaborative",
        {"task": "task/task", "rounds": 1, "command": ["python", "train.py"]},
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
                "workflow_approval",
                {
                    "workflow_session_id": started["workflow_session_id"],
                    "interrupt_id": started["interrupts"][0]["id"],
                    "proposal": "Try a larger learning rate.",
                    "response": {"event": "finish"},
                },
            )
            status = await client.call_tool(
                "workflow_status",
                {"workflow_session_id": started["workflow_session_id"]},
            )
            return approval, status

    approval, status = anyio.run(exercise)
    assert approval.structured_content == {"decision": "cancel"}
    assert status.structured_content["status"] == "waiting"


def test_mcp_approval_supports_other_workflows(tmp_path) -> None:
    config = Project.create(tmp_path, install_skill=False)
    directory = config.workflows_dir / "review"
    directory.mkdir()
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
            approval = await client.call_tool(
                "workflow_approval",
                {
                    "workflow_session_id": started["workflow_session_id"],
                    "interrupt_id": started["interrupts"][0]["id"],
                    "proposal": "Accept the value",
                    "response": {"value": "approved"},
                },
            )
            return await client.call_tool(
                "workflow_resume",
                {
                    "request": {
                        "workflow_session_id": started["workflow_session_id"],
                        "response": approval.structured_content,
                        "checkpoint_id": started["checkpoint_id"],
                    }
                },
            )

    result = anyio.run(exercise)
    assert result.structured_content["result"] == "approved"


def test_approval_result_can_resume_after_server_restart(tmp_path) -> None:
    config = Project.create(tmp_path, install_skill=False)
    config.vault.create("task/task", type="task", title="task")
    first = start_workflow(
        "collaborative",
        {"task": "task/task", "rounds": 1, "command": ["python", "train.py"]},
        project=config,
    )

    async def elicit(_context, _params):
        return types.ElicitResult(action="accept", content={"decision": "stop"})

    async def exercise():
        async with Client(create_server(config), elicitation_callback=elicit) as client:
            approval = await client.call_tool(
                "workflow_approval",
                {
                    "workflow_session_id": first["workflow_session_id"],
                    "interrupt_id": first["interrupts"][0]["id"],
                    "proposal": "Stop the workflow",
                    "response": {},
                },
            )
        async with Client(create_server(config)) as client:
            return await client.call_tool(
                "workflow_resume",
                {
                    "request": {
                        "workflow_session_id": first["workflow_session_id"],
                        "response": approval.structured_content,
                        "checkpoint_id": first["checkpoint_id"],
                    }
                },
            )

    resumed = anyio.run(exercise)
    assert resumed.structured_content["status"] == "completed"
