from __future__ import annotations

import asyncio
import subprocess
import sys
import time
from pathlib import Path

import pytest

from hiraku.core.config import HirakuConfig
from hiraku.core.errors import ValidationError
from hiraku.core.record import Record
from hiraku.mcp import (
    Analysis,
    ExperimentProposal,
    LiteratureReview,
    LiteratureSource,
    ProposedEntrypoint,
    ResearchTask,
    commit_implementation,
    create_server,
    create_task,
    get_launch_status,
    launch_experiment,
    list_runs,
    program_resource,
    project_resource,
    propose_experiment,
    propose_experiment_prompt,
    record_analysis,
    record_resource,
    save_literature_review,
)
from hiraku.mcp import mcp as module_mcp


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
    git(tmp_path, "config", "commit.gpgsign", "false")
    git(tmp_path, "add", ".")
    git(tmp_path, "commit", "-m", "initialize heart disease example")
    mcp = create_server(config)
    created_task = create_task(
        ResearchTask(
            id="heart-disease",
            goal="Compare models for heart disease classification.",
            metrics=[
                {"name": "val/log_loss", "direction": "minimize"},
                {"name": "val/accuracy", "direction": "maximize"},
            ],
            protocol="Use a fixed stratified split and seed.",
            experiment_budget=3,
        )
    )
    assert created_task["type"] == "task"
    assert Record.load(config.records_dir / "heart-disease/heart-disease.md").goal
    review = save_literature_review(
        "heart-disease",
        LiteratureReview(
            context_id="heart-disease-literature-review",
            scope="Small-data classification methods",
            synthesis="A regularized linear baseline is a useful reference.",
            candidate_directions=["Compare a shallow tree model."],
            limitations="Only discovery summaries were reviewed.",
            sources=[
                LiteratureSource(
                    title="A classification study",
                    identifier="arXiv:2501.00001",
                    url="https://arxiv.org/abs/2501.00001",
                    year=2025,
                    verification_status="discovery_summary",
                    evidence_notes="Search result summary only.",
                )
            ],
        ),
    )
    assert review["task_id"] == "heart-disease"
    expected_tools = {
        "list_runs",
        "create_task",
        "propose_experiment",
        "commit_implementation",
        "launch_experiment",
        "get_launch_status",
        "record_analysis",
        "save_literature_review",
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
    assert "# Autoresearch" in prompt_output
    assert "fixed stratified split and seed" in prompt_output
    assert "Compare models for heart disease classification." in prompt_output
    assert "heart-disease-literature-review" in prompt_output
    assert "discovery_summary" in prompt_output
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
    proposed = Record.load(config.records_dir / "heart-disease/logistic-guided.md")
    assert proposed.status == "proposed"
    assert not {
        "approval_status",
        "approved_by",
        "approval_method",
        "approved_at_head",
        "campaign_authorization_id",
    }.intersection(proposed.to_dict())

    source = tmp_path / "train.py"
    source.write_text(source.read_text(encoding="utf-8") + "\n# approved experiment\n")
    git(tmp_path, "add", "train.py")
    git(tmp_path, "config", "commit.gpgSign", "true")
    with pytest.raises(ValidationError, match="outside the project"):
        commit_implementation(
            "logistic-guided", ["../outside.py"], "test: outside project"
        )

    (tmp_path / "unrelated.py").write_text("print('leave unstaged')\n")
    git(tmp_path, "add", "unrelated.py")
    with pytest.raises(ValidationError, match="unrelated staged paths"):
        commit_implementation(
            "logistic-guided", ["train.py"], "test: reject unrelated staged file"
        )
    git(tmp_path, "reset", "--", "unrelated.py")

    committed = commit_implementation(
        "logistic-guided",
        ["train.py"],
        "test: retain seeded logistic baseline",
    )
    assert committed["commit"] == git(tmp_path, "rev-parse", "HEAD")
    assert committed["committed_paths"] == ["train.py"]
    launched = launch_experiment(
        "logistic-guided",
        "train",
        args={"model": "logistic"},
        cpu_time_limit_seconds=120,
    )
    assert launched["status"] in {"queued", "running"}
    deadline = time.monotonic() + 30
    while launched["status"] in {"queued", "running"}:
        if time.monotonic() >= deadline:
            raise AssertionError(f"Launch did not finish: {launched}")
        time.sleep(0.05)
        launched = get_launch_status(launched["launch_id"])
    assert launched["status"] == "completed", launched
    assert launched["returncode"] == 0
    assert git(tmp_path, "show", "--format=", "--name-only", "HEAD").splitlines() == [
        "train.py"
    ]
    repeated = launch_experiment("logistic-guided", "train", args={"model": "logistic"})
    assert repeated["status"] in {"queued", "running"}
    deadline = time.monotonic() + 30
    while repeated["status"] in {"queued", "running"}:
        if time.monotonic() >= deadline:
            raise AssertionError(f"Launch did not finish: {repeated}")
        time.sleep(0.05)
        repeated = get_launch_status(repeated["launch_id"])
    assert repeated["status"] == "completed", repeated
    assert repeated["returncode"] == 0
    assert Record.load(
        config.records_dir / "heart-disease/logistic-guided.md"
    ).status == ("implemented")
    runs = list_runs("logistic-guided")
    assert isinstance(runs, list) and len(runs) == 2, repr(runs)
    assert all(run["record_id"] == "logistic-guided" for run in runs)
    assert all(run["commit"] == committed["commit"] for run in runs)
    conclusion = record_analysis(
        "logistic-guided",
        Analysis(
            run_ids=[runs[0]["id"]],
            conclusion="The run completed with finite validation metrics.",
            decision="keep",
        ),
    )
    assert conclusion["status"] == "analyzed"
    assert conclusion["run_ids"] == [runs[0]["id"]]


def test_create_task_persists_tracking_backend_without_connecting(
    tmp_path: Path,
) -> None:
    config = HirakuConfig.init(tmp_path, install_skill=False)
    create_server(config)

    result = create_task(
        ResearchTask(
            id="tracked-task",
            goal="Keep task tracking configuration with task state.",
            metrics=[{"name": "val/accuracy", "direction": "maximize"}],
            protocol="Fixed protocol.",
            experiment_budget=2,
            tracking_backend={"type": "wandb", "project": "tracked-task"},
        )
    )

    task = Record.load(config.records_dir / "tracked-task/tracked-task.md")
    assert result["type"] == "task"
    assert task.tracking_backend == {"type": "wandb", "project": "tracked-task"}
