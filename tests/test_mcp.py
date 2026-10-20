from __future__ import annotations

import asyncio
import base64
import json
import sys
from pathlib import Path

import pytest
from click.testing import CliRunner

pytest.importorskip("mcp")

from mcp import ClientSession, StdioServerParameters  # noqa: E402
from mcp.client.stdio import stdio_client  # noqa: E402
from mcp.server.mcpserver.exceptions import ResourceError, ToolError  # noqa: E402

from hiraku.cli import main  # noqa: E402
from hiraku.config import HirakuConfig  # noqa: E402
from hiraku.context import load_context  # noqa: E402
from hiraku.mcp import create_server  # noqa: E402
from hiraku.record import load_record  # noqa: E402


def test_tools_and_validation(tmp_path: Path) -> None:
    config = HirakuConfig.init(tmp_path)
    server = create_server(config)

    async def exercise() -> None:
        async def call(name: str, **args):
            return await server.call_tool(name, args)

        await call(
            "record_create",
            record_id="root",
            fields={
                "status": "new",
                "entrypoints": {"train": {"command": ["echo"]}},
            },
        )
        await call("record_create", record_id="child", parent_id="root")
        await call("record_update", record_id="child", fields={"status": "done"})
        assert load_record("child", config=config).status == "done"
        assert load_record("child", config=config).entrypoint(
            "train", config=config
        ).command == ["echo"]
        for name, args in [
            ("record_create", {"record_id": "bad", "parent_id": "missing"}),
            ("record_create", {"record_id": "../escape"}),
            ("record_create", {"record_id": "root"}),
            ("record_update", {"record_id": "child", "fields": {"id": "renamed"}}),
            ("record_update", {"record_id": "child", "fields": {"related_to": ["no"]}}),
            ("record_delete", {"record_id": "root"}),
        ]:
            with pytest.raises(ToolError):
                await server.call_tool(name, args)
        assert load_record("child", config=config).related_to == []
        await call("record_reparent", record_id="child", parent_id=None)
        await call("record_delete", record_id="child")
        await call("context_create", context_id="sources", markdown="Evidence")
        with pytest.raises(ToolError):
            await call("context_create", context_id="sources")
        with pytest.raises(ToolError):
            await call("context_create", context_id="../escape")
        await call(
            "context_update", context_id="sources", fields={}, markdown="Updated"
        )
        assert load_context("sources", config=config).markdown == "Updated"
        await call("context_show", context_id="sources")
        await call("context_list")
        await call("record_show", record_id="root")
        await call("record_list")
        await call("context_delete", context_id="sources")
        assert list(config.contexts_dir.glob("*/context.md")) == []

    asyncio.run(exercise())


def test_resources_and_prompt(tmp_path: Path) -> None:
    config = HirakuConfig.init(tmp_path)
    server = create_server(config)

    async def exercise() -> None:
        await server.call_tool("record_create", {"record_id": "demo"})
        await server.call_tool(
            "context_create",
            {
                "context_id": "literature",
                "markdown": "Curated evidence",
            },
        )
        folder = config.contexts_dir / "literature"
        (folder / "papers").mkdir()
        pdf = b"%PDF-1.4\nsource bytes"
        (folder / "papers" / "paper one.pdf").write_bytes(pdf)
        outside = tmp_path / "private.txt"
        outside.write_text("secret")
        (folder / "outside.txt").symlink_to(outside)
        catalog = list(await server.read_resource("hiraku://catalog"))[0]
        uris = json.loads(catalog.content)
        assert "hiraku://sources/literature/papers%2Fpaper%20one.pdf" in uris
        assert "hiraku://sources/literature/outside.txt" not in uris
        content = list(
            await server.read_resource(
                "hiraku://sources/literature/papers%2Fpaper%20one.pdf"
            )
        )[0]
        assert content.content == pdf
        assert content.mime_type == "application/pdf"
        for path in ["outside.txt", "%2E%2E%2F%2E%2E%2Fprivate.txt", "%2Fetc%2Fpasswd"]:
            # Encoded traversal may already fail to match the URI template.
            with pytest.raises(
                ResourceError, match="Source must be a file|Unknown resource"
            ):
                await server.read_resource(f"hiraku://sources/literature/{path}")
        for uri in [
            "hiraku://records/demo",
            "hiraku://contexts/literature",
        ]:
            assert list(await server.read_resource(uri))[0].content
        with pytest.raises(ResourceError, match="'missing' not found"):
            await server.read_resource("hiraku://records/missing")
        prompt = await server.get_prompt(
            "propose_implementation",
            {
                "record_id": "demo",
                "context_ids": "literature",
            },
        )
        assert "Curated evidence" in prompt.messages[0].content.text

    asyncio.run(exercise())


def test_launch_capture_and_attribution(tmp_path: Path) -> None:
    config = HirakuConfig.init(tmp_path)
    dispatcher = config.dispatch_dir / "test.sh"
    dispatcher.write_text(
        '#!/bin/sh\nprintf "%s\\n" "$HIRAKU_RECORD_ID:$HIRAKU_ENTRYPOINT"\n'
        'printf "diagnostic\\n" >&2\nexit 7\n'
    )
    dispatcher.chmod(0o755)
    server = create_server(config)

    async def exercise() -> None:
        await server.call_tool(
            "record_create",
            {
                "record_id": "demo",
                "fields": {
                    "entrypoints": {"train": {"command": ["echo"], "args": {"n": 1}}},
                },
            },
        )
        preview = await server.call_tool(
            "launch_entrypoint",
            {
                "record_id": "demo",
                "entrypoint_name": "train",
                "dry_run": True,
                "args": {"n": 2},
            },
        )
        assert preview.structured_content["command"] == ["echo", "--n", "2"]
        result = await server.call_tool(
            "launch_entrypoint",
            {
                "record_id": "demo",
                "entrypoint_name": "train",
                "dispatch": "test.sh",
            },
        )
        assert result.structured_content == {
            "returncode": 7,
            "stdout": "demo:train\n",
            "stderr": "diagnostic\n",
        }
        with pytest.raises(ToolError):
            await server.call_tool(
                "launch_entrypoint",
                {
                    "record_id": "demo",
                    "entrypoint_name": "missing",
                },
            )

    asyncio.run(exercise())


def test_runs(tmp_path: Path) -> None:
    config = HirakuConfig.init(tmp_path)
    (tmp_path / "runs.csv").write_text(
        "id,record_id,metric.acc\na,task,0.5\nb,task,0.9\nc,other,0.7\n"
    )
    server = create_server(config)

    async def exercise() -> None:
        await server.call_tool(
            "record_create",
            {
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
    config = HirakuConfig.init(tmp_path)
    source = config.contexts_dir / "papers"
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
                        "context_create",
                        {
                            "context_id": "papers",
                            "markdown": "Literature",
                        },
                    )
                ).is_error
                pdf = b"%PDF-test"
                (source / "paper.pdf").write_bytes(pdf)
                result = await client.read_resource("hiraku://sources/papers/paper.pdf")
                assert result.contents[0].mime_type == "application/pdf"
                assert base64.b64decode(result.contents[0].blob) == pdf
                error = await client.call_tool("record_show", {"record_id": "missing"})
                assert error.is_error
                assert "missing" in error.content[0].text

    asyncio.run(exercise())


def test_cli_project_error(tmp_path: Path) -> None:
    runner = CliRunner()
    with runner.isolated_filesystem(temp_dir=tmp_path):
        result = runner.invoke(main, ["mcp"])
    assert result.exit_code == 1
    assert "No hiraku.yml" in result.output
