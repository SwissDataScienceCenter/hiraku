from __future__ import annotations

import asyncio
import subprocess
import sys
from pathlib import Path

from hiraku.config import HirakuConfig
from hiraku.mcp import (
    Analysis,
    ExperimentProposal,
    ProposedEntrypoint,
    approve_experiment,
    commit_implementation,
    create_server,
    launch_experiment,
    list_runs,
    program_resource,
    project_resource,
    propose_experiment,
    propose_experiment_prompt,
    record_analysis,
    record_resource,
)
from hiraku.mcp import mcp as module_mcp
from hiraku.record import Record


def git(path: Path, *arguments: str) -> str:
    return subprocess.run(
        ["git", *arguments], cwd=path, text=True, capture_output=True, check=True
    ).stdout.strip()


def test_mcp_guided_heart_disease_cycle(tmp_path: Path) -> None:
    """Exercise one experiment through the same typed MCP tool registry."""
    config = HirakuConfig.init(tmp_path, example="heart-disease")
    git(tmp_path, "init", "-b", "main")
    git(tmp_path, "config", "user.email", "test@example.invalid")
    git(tmp_path, "config", "user.name", "Test")
    git(tmp_path, "add", ".")
    git(tmp_path, "commit", "-m", "initialize heart disease example")
    Record.create(
        "heart-disease",
        type="task",
        metrics=[
            {"name": "val/log_loss", "direction": "minimize"},
            {"name": "val/accuracy", "direction": "maximize"},
        ],
        config=config,
    )
    mcp = create_server(config)
    expected_tools = {
        "list_runs",
        "propose_experiment",
        "approve_experiment",
        "commit_implementation",
        "launch_experiment",
        "record_analysis",
    }
    assert {tool.name for tool in module_mcp._tool_manager.list_tools()} == (
        expected_tools
    )
    assert {item.name for item in asyncio.run(module_mcp.list_resources())} == {
        "project"
    }
    assert {item.name for item in asyncio.run(module_mcp.list_prompts())} == {
        "autoresearch",
        "interactive",
        "literature-review",
        "propose-experiment",
    }
    autoresearch_prompt = asyncio.run(
        mcp.get_prompt("autoresearch", {"task": "heart-disease"})
    )
    prompt_output = str(autoresearch_prompt.messages)
    assert "hiraku://records/heart-disease" in prompt_output
    assert "hiraku://programs/autoresearch" in prompt_output
    tool_names = {tool.name for tool in mcp._tool_manager.list_tools()}
    assert tool_names == expected_tools
    resources = {resource.name for resource in asyncio.run(mcp.list_resources())}
    templates = {
        template.name for template in asyncio.run(mcp.list_resource_templates())
    }
    assert resources == {"project"}
    assert templates == {"records", "programs", "contexts"}
    prompt_messages = propose_experiment_prompt(
        "heart-disease", "Try a gradient-boosted tree."
    )
    assert "Try a gradient-boosted tree." in prompt_messages[0].content.text
    prompt_uris = {
        message.content.uri
        for message in prompt_messages[1:]
        if hasattr(message.content, "uri")
    }
    assert "hiraku://records/heart-disease" in prompt_uris
    assert "hiraku://programs/interactive" in prompt_uris
    assert "tracking_backend" in project_resource()["config"]
    assert "heart-disease" in project_resource()["records"]
    assert "# Interactive" in program_resource("interactive")
    assert '"effective"' in record_resource("heart-disease")

    propose_experiment(
        ExperimentProposal(
            id="logistic-guided",
            parent_id="heart-disease",
            summary="Retain a logistic baseline with fixed seed.",
            hypothesis=(
                "The seeded logistic model produces finite validation metrics."
            ),
            implementation_plan="Use the existing logistic model and fixed seed.",
            evaluation_plan="Compare validation log loss and accuracy.",
            entrypoint=ProposedEntrypoint(
                name="train",
                command=[sys.executable, "train.py"],
                args={"seed": 42, "validation-fraction": 0.25},
            ),
        )
    )
    proposal = record_resource("logistic-guided")
    assert "## Implementation plan" in proposal
    assert "## Evaluation plan" in proposal
    approved = approve_experiment("logistic-guided", "human reviewer")
    assert approved["approval_status"] == "approved"

    source = tmp_path / "train.py"
    source.write_text(source.read_text(encoding="utf-8") + "\n# approved experiment\n")
    git(tmp_path, "add", "train.py")
    git(tmp_path, "config", "commit.gpgSign", "true")
    committed = commit_implementation(
        "logistic-guided",
        ["train.py"],
        "test: retain seeded logistic baseline",
    )
    assert committed["commit"] == git(tmp_path, "rev-parse", "HEAD")
    launched = launch_experiment("logistic-guided", "train", args={"model": "logistic"})
    assert launched["returncode"] == 0
    runs = list_runs("logistic-guided")
    assert isinstance(runs, list), repr(runs)
    assert runs and runs[0]["record_id"] == "logistic-guided"
    assert runs[0]["commit"] == committed["commit"]
    conclusion = record_analysis(
        "logistic-guided",
        Analysis(
            run_ids=[runs[0]["id"]],
            conclusion="The run completed with finite validation metrics.",
            decision="retain",
        ),
    )
    assert conclusion["status"] == "analyzed"
    assert conclusion["run_ids"] == [runs[0]["id"]]
