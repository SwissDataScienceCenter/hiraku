from __future__ import annotations

import subprocess
import sys
from pathlib import Path

from hiraku.config import HirakuConfig
from hiraku.mcp import (
    Analysis,
    ExperimentProposal,
    HirakuMCPServer,
    ProposedEntrypoint,
)
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
    mcp = HirakuMCPServer(config)
    tool_names = {tool.name for tool in mcp.server._tool_manager.list_tools()}
    assert tool_names == {
        "get_record",
        "resolve_record",
        "list_children",
        "list_runs",
        "propose_experiment",
        "approve_experiment",
        "commit_implementation",
        "launch_experiment",
        "record_analysis",
    }

    mcp.propose_experiment(
        ExperimentProposal(
            id="logistic-guided",
            parent_id="heart-disease",
            summary="Retain a logistic baseline with fixed seed.",
            hypothesis=(
                "The seeded logistic model produces finite validation metrics."
            ),
            entrypoint=ProposedEntrypoint(
                name="train",
                command=[sys.executable, "train.py"],
                args={"seed": 42, "validation-fraction": 0.25},
            ),
        )
    )
    approved = mcp.approve_experiment("logistic-guided", "human reviewer")
    assert approved["approval_status"] == "approved"

    source = tmp_path / "train.py"
    source.write_text(source.read_text(encoding="utf-8") + "\n# approved experiment\n")
    git(tmp_path, "add", "train.py")
    git(tmp_path, "config", "commit.gpgSign", "true")
    committed = mcp.commit_implementation(
        "logistic-guided",
        ["train.py"],
        "test: retain seeded logistic baseline",
    )
    assert committed["commit"] == git(tmp_path, "rev-parse", "HEAD")
    launched = mcp.launch_experiment(
        "logistic-guided", "train", args={"model": "logistic"}
    )
    assert launched["returncode"] == 0
    runs = mcp.list_runs("logistic-guided")
    assert isinstance(runs, list), repr(runs)
    assert runs and runs[0]["record_id"] == "logistic-guided"
    assert runs[0]["commit"] == committed["commit"]
    conclusion = mcp.record_analysis(
        "logistic-guided",
        Analysis(
            run_ids=[runs[0]["id"]],
            conclusion="The run completed with finite validation metrics.",
            decision="retain",
        ),
    )
    assert conclusion["status"] == "analyzed"
    assert conclusion["run_ids"] == [runs[0]["id"]]
