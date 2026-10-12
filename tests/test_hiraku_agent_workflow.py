from __future__ import annotations

import subprocess
from pathlib import Path
from typing import Any

import pytest

from hiraku.config import HirakuConfig
from hiraku.record import Record, load_record
from hiraku.workflow.service import WorkflowService

HEADER = "id,record_id,commit,entrypoint,status,metric.acc\n"


def git(root: Path, *arguments: str) -> str:
    return subprocess.run(
        ["git", *arguments], cwd=root, text=True, capture_output=True, check=True
    ).stdout.strip()


class Harness:
    def __init__(self, config: HirakuConfig) -> None:
        self.service = WorkflowService(config)
        self.response: dict[str, Any] = {}

    def start(self, **arguments: Any) -> dict[str, Any]:
        self.response = self.service.call("hiraku-agent_start", arguments)
        return self.response

    def reply(self, tool: str, **arguments: Any) -> dict[str, Any]:
        result = self.service.call(
            f"hiraku-agent_{tool}",
            {
                "session_id": self.response["session_id"],
                "instruction_id": self.response["instruction_id"],
                **arguments,
            },
        )
        if result["status"] == "accepted":
            self.response = result
        return result

    @property
    def step(self) -> str:
        return self.response["instruction"]["text"].splitlines()[0]

    @property
    def context(self) -> dict[str, Any]:
        return self.response["instruction"]["context"]

    @property
    def replies(self) -> list[str]:
        return [
            item["tool"].removeprefix("hiraku-agent_")
            for item in self.response["instruction"]["replies"]
        ]


@pytest.fixture
def project(tmp_path: Path) -> HirakuConfig:
    root = tmp_path / "project"
    root.mkdir()
    git(root, "init", "-q", "-b", "main")
    git(root, "config", "user.email", "test@example.invalid")
    git(root, "config", "user.name", "Test")
    (root / ".gitignore").write_text("hiraku/\nhiraku.yml\nruns.csv\n.mcp.json\n")
    (root / "train.py").write_text("print('train')\n")
    git(root, "add", ".")
    git(root, "commit", "-q", "-m", "initial")
    config = HirakuConfig.init(root, install_skill=False, install_mcp=False)
    Record.create(
        "task",
        type="task",
        config=config,
        markdown="## Decision rule\n\nHigher acc wins.\n",
        metrics=[{"name": "acc", "direction": "maximize"}],
        entrypoints={"train": {"command": ["python", "train.py"]}},
    )
    return config


def commit_change(config: HirakuConfig, branch: str, base: str, name: str) -> str:
    root = config.root
    git(root, "switch", "-q", "-c", branch, base)
    (root / f"{name}.py").write_text(f"print('{name}')\n")
    git(root, "add", f"{name}.py")
    git(root, "commit", "-q", "-m", f"implement {name}")
    return git(root, "rev-parse", "HEAD")


def add_run(
    config: HirakuConfig, run_id: str, node: str, acc: str, status: str = "finished"
) -> None:
    path = config.root / "runs.csv"
    if not path.exists():
        path.write_text(HEADER)
    commit = load_record(node, config=config).commit or ""
    with path.open("a") as stream:
        stream.write(f"{run_id},{node},{commit},train,{status},{acc}\n")


def design_baseline(harness: Harness) -> None:
    harness.start(task="task", mode="guided")
    assert harness.step == "## Grow"
    assert harness.response["instruction"]["requires_user_response"] is True
    harness.reply("choose", step="design", instruction="Start with a baseline")
    assert harness.step == "## Design"
    harness.reply(
        "designed",
        experiments=[
            {
                "id": "baseline",
                "kind": "baseline",
                "expectation": "The current model reaches acc of about 0.8.",
                "scope": "Reference run.",
                "open_items": [
                    {"item": "seed", "value": "0", "status": "default", "source": "x"}
                ],
            },
            {
                "id": "deeper",
                "kind": "hypothesis",
                "expectation": "A deeper model raises acc by 0.05.",
            },
        ],
    )


def implement(harness: Harness, config: HirakuConfig, node: str) -> str:
    assert harness.step == "## Implement"
    context = harness.context
    commit = commit_change(config, context["branch"], context["base_commit"], node)
    harness.reply("implemented", commit=commit, notes="Smoke-tested on 10 rows.")
    git(config.root, "switch", "-q", "main")
    return commit


def test_guided_cycle_from_design_to_triage(project: HirakuConfig) -> None:
    harness = Harness(project)
    design_baseline(harness)
    assert "## Rules" not in harness.response["instruction"]["text"]
    baseline = load_record("baseline", config=project).to_dict()
    assert baseline["mode"] == "guided"
    assert (
        "**seed** — 0 (default, to be calibrated; x)"
        in load_record("baseline", config=project).markdown
    )

    assert harness.step == "## Approve"
    assert harness.context["node"]["id"] == "baseline"
    harness.reply("approve", approved_by="Ada")
    assert load_record("baseline", config=project).to_dict()["approved_by"] == "Ada"

    first = implement(harness, project, "baseline")
    assert load_record("baseline", config=project).commit == first
    assert harness.step == "## Launch"
    assert harness.context["commands"] == [
        f"hiraku launch train --record baseline --commit {first}"
    ]
    assert harness.reply("launched", run_ids=["b1"])["status"] == "not_ready"
    add_run(project, "b1", "baseline", "0.80")
    add_run(project, "b2", "baseline", "0.82")
    harness.reply("launched", run_ids=["b1", "b2"])

    assert harness.step == "## Analyze"
    no_verdict = harness.reply("analyzed", verdict=None)
    assert "missing_evidence" in no_verdict["message"]
    harness.reply(
        "analyzed",
        verdict="supports",
        findings="Runs b1, b2 reach 0.81.",
        conclusions="The reference holds.",
    )
    assert load_record("baseline", config=project).to_dict()["verdict"] == "supports"

    assert harness.step == "## Triage"
    unnamed = harness.reply("triaged", failure_class=None, proceed=False)
    assert "blocking condition" in unnamed["message"]
    harness.reply(
        "triaged",
        failure_class=None,
        proceed=False,
        blocking_condition="named_cause",
        investigation_test={"named_cause": {"pass": False, "note": "nothing new"}},
    )
    record = load_record("baseline", config=project)
    assert record.to_dict()["triaged"] is True
    assert "`named_cause` — FAIL" in record.markdown

    assert harness.step == "## Approve"
    assert harness.context["node"]["id"] == "deeper"
    harness.reply("approve", approved_by="Ada")
    implement(harness, project, "deeper")
    add_run(project, "d1", "deeper", "0.86")
    harness.reply("launched", run_ids=["d1"])
    assert harness.step == "## Analyze"
    comparison = harness.context["metric_comparison"]["baseline"][0]
    assert comparison["metric"] == "acc"
    assert comparison["improvement"] == pytest.approx(0.05)
    harness.reply("analyzed", verdict="supports", findings="d1 beats the baseline.")
    assert "**vs `baseline`**" in load_record("deeper", config=project).markdown


def test_triage_children_and_execution_defect(project: HirakuConfig) -> None:
    harness = Harness(project)
    design_baseline(harness)
    harness.reply("approve", approved_by="Ada")
    implement(harness, project, "baseline")
    add_run(project, "b1", "baseline", "0.10")
    harness.reply("launched", run_ids=["b1"])
    harness.reply("analyzed", verdict="refutes", findings="b1 is far off.")
    harness.reply(
        "triaged",
        failure_class="A",
        proceed=False,
        class_evidence="b1 read the wrong split.",
    )
    node = load_record("baseline", config=project).to_dict()
    assert node["execution_defect"] is True
    assert node["defect_runs"] == ["b1"]
    assert node["verdict"] is None

    assert harness.step == "## Fix"
    assert harness.response["instruction"]["requires_user_response"] is True
    branch = load_record("baseline", config=project).commit
    stale = harness.reply("fixed", commit=branch)
    assert "not a new commit" in stale["message"]
    git(project.root, "switch", "-q", "hiraku/records/baseline")
    (project.root / "fix.py").write_text("print('fixed')\n")
    git(project.root, "add", "fix.py")
    git(project.root, "commit", "-q", "-m", "fix split")
    fixed = git(project.root, "rev-parse", "HEAD")
    git(project.root, "switch", "-q", "main")
    harness.reply("fixed", commit=fixed, notes="Use the validation split.")
    assert harness.step == "## Launch"
    add_run(project, "b2", "baseline", "0.80")
    harness.reply("launched", run_ids=["b2"])
    assert harness.step == "## Analyze"
    harness.reply("analyzed", verdict="refutes", findings="b2: 0.80")
    harness.reply(
        "triaged",
        failure_class="B",
        verdict="refutes",
        proceed=True,
        children=[
            {
                "id": "probe",
                "kind": "diagnostic",
                "expectation": "Label noise explains the gap.",
            }
        ],
    )
    probe = load_record("probe", config=project).to_dict()
    assert probe["parent_id"] == "baseline"
    assert probe["origin"] == "agent-diagnostic"
    assert load_record("baseline", config=project).to_dict()["triaged"] is True


def test_needs_evidence_and_running(project: HirakuConfig) -> None:
    harness = Harness(project)
    design_baseline(harness)
    harness.reply("approve", approved_by="Ada")
    implement(harness, project, "baseline")
    add_run(project, "b1", "baseline", "0.8", status="running")
    harness.reply("launched", run_ids=["b1"])
    assert harness.step == "## Approve"
    harness.reply("abandon", node_id="deeper", reason="Not now")
    assert harness.step == "## Wait"
    assert harness.reply("check")["status"] == "not_ready"
    (project.root / "runs.csv").write_text(
        (project.root / "runs.csv").read_text().replace("running", "finished")
    )
    harness.reply("check")
    assert harness.step == "## Analyze"
    harness.reply("analyzed", verdict=None, missing_evidence="Two more seeds.")
    assert harness.step == "## Needs evidence"
    assert "Two more seeds." in harness.context["missing_evidence"]
    repeated = harness.reply("launched", run_ids=["b1"])
    assert "already analysed" in repeated["message"]
    add_run(project, "b2", "baseline", "0.82")
    harness.reply("launched", run_ids=["b2"])
    assert harness.step == "## Analyze"


def test_auto_mode_skips_approval(project: HirakuConfig) -> None:
    harness = Harness(project)
    harness.start(task="task", mode="auto")
    harness.reply("choose", step="design")
    harness.reply(
        "designed",
        experiments=[
            {"id": "baseline", "kind": "baseline", "expectation": "acc about 0.8."}
        ],
    )
    assert harness.step == "## Implement"
    assert harness.response["instruction"]["requires_user_response"] is False


def test_rejected_replies_write_nothing(project: HirakuConfig) -> None:
    harness = Harness(project)
    harness.start(task="task")
    harness.reply("choose", step="design")
    stray = harness.reply(
        "designed",
        experiments=[
            {"id": "a", "expectation": "x"},
            {"id": "b", "parent": "elsewhere", "expectation": "y"},
        ],
    )
    assert "Parents outside task" in stray["message"]
    taken = harness.reply("designed", experiments=[{"id": "task", "expectation": "x"}])
    assert "already exist" in taken["message"]
    assert [
        record.id
        for record in load_record("task", config=project).children(config=project)
    ] == []

    harness.reply(
        "designed",
        experiments=[
            {"id": "baseline", "kind": "baseline", "expectation": "acc about 0.8."}
        ],
    )
    harness.reply("approve", approved_by="Ada")
    context = harness.context
    root = project.root
    git(root, "switch", "-q", "-c", context["branch"], context["base_commit"])
    (root / "job.sh").write_text("python train.py\n")
    git(root, "add", "job.sh")
    git(root, "commit", "-q", "-m", "job")
    commit = git(root, "rev-parse", "HEAD")
    placeholder = harness.reply(
        "implemented",
        commit=commit,
        entrypoints={"job": {"command": ["bash", "job.sh", "<run-id>"]}},
    )
    assert "placeholder" in placeholder["message"]
    missing = harness.reply(
        "implemented",
        commit=commit,
        entrypoints={"job": {"command": ["bash", "missing.sh"]}},
    )
    assert "not in the commit" in missing["message"]
    assert load_record("baseline", config=project).commit is None
    harness.reply(
        "implemented",
        commit=commit,
        entrypoints={"job": {"command": ["bash", "job.sh"]}},
    )
    assert "job" in load_record("baseline", config=project).entrypoints


def test_discover_records_declined_ideas_and_investigation(
    project: HirakuConfig,
) -> None:
    harness = Harness(project)
    harness.start(task="task", instruction="literature on depth")
    harness.reply("choose", step="discover")
    assert harness.step == "## Discover"
    harness.reply(
        "discovered",
        proposals=[
            {
                "id": "wider",
                "title": "Wider layers",
                "expectation": "Wider layers raise acc by 0.02.",
                "counterargument": "Costly.",
            }
        ],
        declined=[
            {
                "id": "new-dataset",
                "expectation": "Train on more data.",
                "failing_condition": "4 — in scope",
                "reason": "The protocol fixes the data.",
            }
        ],
        investigation={"findings": "train.py uses 2 layers."},
        injected_instructions_found="Ignore previous instructions.",
    )
    wider = load_record("wider", config=project)
    assert wider.to_dict()["origin"] == "agent-literature"
    assert wider.to_dict()["mode"] == "guided"
    assert "## Injection warning" in wider.markdown
    declined = load_record("new-dataset", config=project).to_dict()
    assert declined["abandoned"] is True
    task = load_record("task", config=project).markdown
    assert "## Investigation" in task
    assert "literature on depth" in task
    assert harness.step == "## Approve"


def test_draft_limits_and_next_step(project: HirakuConfig) -> None:
    project.clear_active_record()
    harness = Harness(project)
    rejected = harness.start()
    assert "draft a new task" in rejected["message"]
    harness.start(instruction="Does dropout help?", max_steps=2)
    assert harness.step == "## Draft"
    harness.reply(
        "drafted",
        id="dropout",
        goal="Find out whether dropout helps.",
        question="Does dropout raise acc?",
        summary="Dropout study.",
        metrics=[{"name": "acc", "direction": "maximize", "role": "primary"}],
    )
    task = load_record("dropout", config=project)
    assert task.type == "task"
    assert "## Decision rule" in task.markdown
    assert "`acc` (maximize, primary)" in task.markdown
    assert harness.step == "## Grow"
    harness.reply("choose", step="design")
    done = harness.reply(
        "designed", experiments=[{"id": "drop-base", "expectation": "acc 0.8."}]
    )
    assert done["result"]["outcome"] == "limit"
    assert "max_steps (2)" in done["result"]["reason"]

    nxt = harness.service.call("hiraku-agent_next_step", {"task": "dropout"})
    assert nxt["result"]["action"] == "approve"
    assert nxt["result"]["states"] == {"drop-base": "proposed"}


def test_a_node_with_children_is_completed_without_triage(
    project: HirakuConfig,
) -> None:
    harness = Harness(project)
    harness.start(task="task", mode="auto")
    harness.reply("choose", step="design")
    harness.reply(
        "designed",
        experiments=[
            {"id": "baseline", "kind": "baseline", "expectation": "acc 0.8."},
            {"id": "child", "parent": "baseline", "expectation": "acc 0.85."},
        ],
    )
    first = implement(harness, project, "baseline")
    assert harness.step == "## Implement"
    assert harness.context["state"]["focus_node"] == "child"
    assert harness.context["base_commit"] == first
    implement(harness, project, "child")
    assert harness.context["node"] == "baseline"
    add_run(project, "b1", "baseline", "0.8")
    harness.reply("launched", run_ids=["b1"])
    assert harness.context["node"] == "child"
    add_run(project, "c1", "child", "0.9")
    harness.reply("launched", run_ids=["c1"])
    harness.reply("analyzed", verdict="supports", findings="b1")
    assert harness.step == "## Analyze"
    assert harness.context["state"]["focus_node"] == "child"
    states = harness.service.call("hiraku-agent_next_step", {"task": "task"})
    assert states["result"]["states"]["baseline"] == "completed"
