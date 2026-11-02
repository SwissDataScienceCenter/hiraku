from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path

import pytest
from click.testing import CliRunner

from hiraku.record import open_workspace

pytest.importorskip("mcp")

from mcp import ClientSession, StdioServerParameters  # noqa: E402
from mcp.client.stdio import stdio_client  # noqa: E402
from mcp.server.mcpserver.exceptions import ResourceError, ToolError  # noqa: E402

from hiraku.cli import main  # noqa: E402
from hiraku.config import HirakuConfig  # noqa: E402
from hiraku.mcp import create_server  # noqa: E402
from hiraku.record import Record


def test_tools_and_validation(tmp_path: Path) -> None:
    config = HirakuConfig.init(tmp_path, records_path="hiraku/records")
    server = create_server(config)

    async def exercise() -> None:
        async def call(name: str, **args):
            print(f"calling {name}", flush=True)
            return await server.call_tool(name, args)

        await call(
            "record_create",
            record_id="root",
            fields={
                "status": "new",
            },
            path="root/root.md",
        )
        await call(
            "record_create", record_id="child", parent_id="root", path="child/child.md"
        )
        await call("record_update", record_id="child", fields={"status": "done"})
        assert Record.load(open_workspace(config), "child").status == "done"
        for name, args in [
            (
                "record_create",
                {"record_id": "bad", "path": "bad.md", "parent_id": "missing"},
            ),
            ("record_create", {"record_id": "../escape", "path": "bad.md"}),
            ("record_create", {"record_id": "root", "path": "root.md"}),
            ("record_update", {"record_id": "child", "fields": {"id": "renamed"}}),
            ("record_update", {"record_id": "child", "fields": {"parent_id": "no"}}),
            ("record_update", {"record_id": "root", "fields": {"parent_id": "child"}}),
            ("record_update", {"record_id": "child", "fields": {"related_to": ["no"]}}),
            ("record_delete", {"record_id": "root", "path": "root.md"}),
        ]:
            with pytest.raises(ToolError):
                await server.call_tool(name, args)
        assert Record.load(open_workspace(config), "child").related_to == []
        await call("record_update", record_id="child", fields={"parent_id": None})
        assert Record.load(open_workspace(config), "child").parent_id is None
        await call("record_delete", record_id="child")
        await call(
            "record_create",
            record_id="sources",
            fields={"type": "context"},
            markdown="Evidence",
            path="sources/sources.md",
        )
        with pytest.raises(ToolError):
            await call("record_create", record_id="sources", path="sources/sources.md")
        with pytest.raises(ToolError):
            await call(
                "record_create", record_id="../escape", path="../escape/../escape.md"
            )
        await call("record_update", record_id="sources", fields={}, markdown="Updated")
        assert Record.load(open_workspace(config), "sources").markdown == "Updated"
        await call("record_show", record_id="sources")
        await call("record_list")
        await call("record_show", record_id="root")
        result = await call("record_query", query="MATCH (r) RETURN r.id")
        assert result.structured_content["rows"] == [["root"], ["sources"]]
        with pytest.raises(ToolError, match="read-only"):
            await call("record_query", query="MATCH (r) REMOVE r.id RETURN r")
        await call("record_list")
        await call("record_delete", record_id="sources")
        assert list(config.records_dir.glob("*/sources.md")) == []

    asyncio.run(exercise())


def test_resources_and_prompt(tmp_path: Path) -> None:
    config = HirakuConfig.init(tmp_path, records_path="hiraku/records")
    server = create_server(config)

    async def exercise() -> None:
        await server.call_tool(
            "record_create", {"path": "demo/demo.md", "record_id": "demo"}
        )
        await server.call_tool(
            "record_create",
            {
                "path": "literature/literature.md",
                "record_id": "literature",
                "fields": {"type": "context"},
                "markdown": "Curated evidence",
            },
        )
        catalog = list(await server.read_resource("hiraku://catalog"))[0]
        assert set(json.loads(catalog.content)) == {
            "hiraku://records/demo",
            "hiraku://records/literature",
        }
        for uri in [
            "hiraku://records/demo",
            "hiraku://records/literature",
        ]:
            assert list(await server.read_resource(uri))[0].content
        with pytest.raises(ResourceError, match="'missing' not found"):
            await server.read_resource("hiraku://records/missing")
        prompt = await server.get_prompt(
            "propose_implementation",
            {
                "record_id": "demo",
                "related_record_ids": "literature",
            },
        )
        assert "Curated evidence" in prompt.messages[0].content.text

    asyncio.run(exercise())


def test_execute_tool_runs_in_foreground(tmp_path: Path) -> None:
    config = HirakuConfig.init(tmp_path, records_path="hiraku/records")
    server = create_server(config)

    async def exercise() -> None:
        await server.call_tool(
            "record_create", {"path": "demo/demo.md", "record_id": "demo"}
        )
        result = await server.call_tool(
            "execute",
            {
                "record_id": "demo",
                "command": [
                    sys.executable,
                    "-c",
                    "import sys; print('out'); print('err', file=sys.stderr); "
                    "raise SystemExit(7)",
                ],
            },
        )
        assert result.structured_content == {
            "returncode": 7,
            "stdout": "out\n",
            "stderr": "err\n",
        }

    asyncio.run(exercise())


def test_runs(tmp_path: Path) -> None:
    config = HirakuConfig.init(tmp_path, records_path="hiraku/records")
    (tmp_path / "runs.csv").write_text(
        "id,record_id,metric.acc\na,task,0.5\nb,task,0.9\nc,other,0.7\n"
    )
    server = create_server(config)

    async def exercise() -> None:
        await server.call_tool(
            "record_create",
            {
                "path": "task/task.md",
                "record_id": "task",
                "fields": {"metrics": [{"name": "acc", "direction": "maximize"}]},
            },
        )

        async def ids(**args) -> list[str]:
            result = await server.call_tool("runs", {"record_id": "task", **args})
            return [run["id"] for run in result.structured_content["result"]]

        assert await ids(sort="acc") == ["b", "a"]
        assert await ids(sort="acc", ascending=True, limit=1) == ["a"]
        assert sorted(await ids(all_records=True)) == ["a", "b", "c"]
        with pytest.raises(ToolError, match="requires sort"):
            await server.call_tool("runs", {"record_id": "task", "ascending": True})

    asyncio.run(exercise())


def test_stdio_session(tmp_path: Path) -> None:
    config = HirakuConfig.init(tmp_path, records_path="hiraku/records")
    source = config.records_dir / "papers"
    source.mkdir()

    async def exercise() -> None:
        params = StdioServerParameters(
            command=sys.executable,
            args=[
                "-c",
                "from hiraku.cli import main; main()",
                "mcp",
            ],
            cwd=str(tmp_path),
        )
        async with stdio_client(params) as (read, write):  # noqa: SIM117
            async with ClientSession(read, write, read_timeout_seconds=10) as client:
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
                            "path": "papers/papers.md",
                            "record_id": "papers",
                            "fields": {"type": "context"},
                            "markdown": "Literature",
                        },
                    )
                ).is_error
                result = await client.read_resource("hiraku://records/papers")
                assert "Literature" in result.contents[0].text
                error = await client.call_tool("record_show", {"record_id": "missing"})
                assert error.is_error
                assert "missing" in error.content[0].text

    asyncio.run(exercise())


def test_cli_project_error(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    result = CliRunner().invoke(main, ["mcp"])
    assert result.exit_code == 1
    assert "No hiraku.yml" in result.output


def test_cli_serves_project(tmp_path, monkeypatch):
    from unittest.mock import Mock

    config = HirakuConfig.init(
        tmp_path, install_skill=False, records_path="hiraku/records"
    )
    monkeypatch.chdir(tmp_path)
    factory = Mock()
    monkeypatch.setattr("hiraku.mcp.create_server", factory)
    result = CliRunner().invoke(main, ["mcp"])
    assert result.exit_code == 0, result.output
    assert factory.call_args.kwargs == {}
    assert factory.call_args.args[0].path == config.path
    factory.return_value.run.assert_called_once_with(transport="stdio")
