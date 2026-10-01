from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest
from click.testing import CliRunner

from hiraku.agent.action import Action, ActionContext
from hiraku.agent.provider import call_provider as _call_provider
from hiraku.cli import main
from hiraku.config import HirakuConfig
from hiraku.record import Record, list_records, load_record


def _git(root: Path, *arguments: str) -> str:
    return subprocess.run(
        ["git", *arguments], cwd=root, text=True, capture_output=True, check=True
    ).stdout.strip()


def test_draft_creates_under_task_or_experiment(tmp_path: Path, monkeypatch) -> None:
    config = HirakuConfig.init(tmp_path, install_skill=False)
    Record.create("task", type="task", config=config)
    monkeypatch.chdir(tmp_path)
    responses = iter(
        [
            '{"id":"baseline","kind":"hypothesis","body":"Compare backbones"}',
            '{"id":"follow-up","kind":"hypothesis","body":"Follow up"}',
        ]
    )

    def reply(provider, model, prompt, root, effort, **kwargs):
        assert "Compare backbones" in prompt or "Follow up" in prompt
        assert root == tmp_path
        return next(responses)

    monkeypatch.setattr("hiraku.agent.provider.call_provider", reply)
    runner = CliRunner()
    first = runner.invoke(
        main,
        [
            "agent",
            "experiment",
            "draft",
            "--record",
            "task",
            "-m",
            "Compare backbones",
            "--provider",
            "claude",
            "--model",
            "test-model",
            "--json",
        ],
    )
    assert first.exit_code == 0, first.output
    assert json.loads(first.stdout)["origin"] == "bundled"
    second = runner.invoke(
        main,
        [
            "agent",
            "experiment",
            "draft",
            "--record",
            "baseline",
            "-m",
            "Follow up",
            "--json",
        ],
    )
    assert second.exit_code == 0, second.output
    assert (
        load_record("baseline", config=config).parent_id,
        load_record("baseline", config=config).status,
    ) == ("task", "draft")
    assert load_record("follow-up", config=config).parent_id == "baseline"
    assert load_record("baseline", config=config).markdown == "Compare backbones"
    assert load_record("follow-up", config=config).markdown == "Follow up"


def test_task_draft_creates_and_activates_root_task(
    tmp_path: Path, monkeypatch
) -> None:
    config = HirakuConfig.init(tmp_path, install_skill=False)
    Record.create("existing", type="task", config=config)
    monkeypatch.chdir(tmp_path)

    def reply(provider, model, prompt, root, effort, **kwargs):
        assert "{{rules}}" not in prompt
        assert '"existing_tasks": [\n    "existing"' in prompt
        assert "Investigate calibration drift" in prompt
        return json.dumps(
            {
                "id": "calibration-drift",
                "goal": "Improve probability calibration",
                "question": "Does temperature scaling reduce log loss?",
                "summary": "Study calibration drift.",
                "body": "Compare calibrated and uncalibrated predictions.",
                "metrics": [{"name": "log loss", "direction": "minimize"}],
                "notes": "Confirm the validation split.",
            }
        )

    monkeypatch.setattr("hiraku.agent.provider.call_provider", reply)
    runner = CliRunner()
    missing = runner.invoke(main, ["agent", "task", "draft"])
    assert missing.exit_code != 0
    assert "requires -m/--message" in missing.output
    result = runner.invoke(
        main,
        [
            "agent",
            "task",
            "draft",
            "-m",
            "Investigate calibration drift",
            "--json",
        ],
    )
    assert result.exit_code == 0, result.output
    payload = json.loads(result.stdout)
    task = load_record("calibration-drift", config=config)
    assert task.parent_id is None
    assert task.status == "draft"
    assert task.markdown == "Compare calibrated and uncalibrated predictions."
    assert HirakuConfig.discover(tmp_path).active_record_id == task.id
    assert "Confirm the validation split." in result.stderr
    assert payload["record"] == task.id


def test_implement_binds_hash_and_transitions(tmp_path: Path, monkeypatch) -> None:
    _git(tmp_path, "init", "-b", "main")
    _git(tmp_path, "config", "user.name", "Test")
    _git(tmp_path, "config", "user.email", "test@example.invalid")
    config = HirakuConfig.init(tmp_path, install_skill=False)
    (tmp_path / ".gitignore").write_text("/hiraku/\n/hiraku.yml\n")
    Record.create("task", type="task", config=config)
    Record.create("baseline", parent_id="task", type="experiment", config=config)
    _git(tmp_path, "add", ".")
    _git(tmp_path, "commit", "-m", "initialize")
    monkeypatch.chdir(tmp_path)

    def reply(provider, model, prompt, root, effort, **kwargs):
        assert "## User instruction" not in prompt
        assert "Do not" in prompt
        assert '"id": "baseline"' in prompt
        assert "{{state}}" not in prompt
        (root / "model.py").write_text("MODEL = 1\n")
        return "Implemented"

    monkeypatch.setattr("hiraku.agent.provider.call_provider", reply)
    runner = CliRunner()
    succeeded = runner.invoke(
        main,
        [
            "agent",
            "experiment",
            "implement",
            "--record",
            "baseline",
            "--json",
        ],
    )
    assert succeeded.exit_code == 0, succeeded.output
    record = load_record("baseline", config=config)
    assert record.status == "implemented"
    assert record.commit == _git(tmp_path, "rev-parse", "HEAD")
    assert "model.py" in _git(tmp_path, "show", "--format=", "--name-only", "HEAD")
    committed_files = _git(tmp_path, "show", "--format=", "--name-only", "HEAD")
    assert "agent-logs" not in committed_files
    payload = json.loads(succeeded.stdout)
    events = [
        json.loads(line)
        for line in (Path(payload["log"]) / "events.jsonl").read_text().splitlines()
    ]
    assert {"file_changed", "commit_created", "commit_bound", "status_changed"} <= {
        event["type"] for event in events
    }
    assert "Changed model.py" in succeeded.stderr
    assert (
        runner.invoke(
            main,
            [
                "agent",
                "experiment",
                "implement",
                "--record",
                "baseline",
            ],
        ).exit_code
        != 0
    )


def test_implement_rejects_dirty_start(tmp_path: Path, monkeypatch) -> None:
    _git(tmp_path, "init", "-b", "main")
    config = HirakuConfig.init(tmp_path, install_skill=False)
    Record.create("task", type="task", config=config)
    Record.create("baseline", parent_id="task", type="experiment", config=config)
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(
        "hiraku.agent.provider.call_provider",
        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("provider ran")),
    )
    result = CliRunner().invoke(
        main,
        [
            "agent",
            "experiment",
            "implement",
            "--record",
            "baseline",
        ],
    )
    assert result.exit_code != 0
    assert "clean starting tree" in result.output
    assert load_record("baseline", config=config).status == "draft"
    logs = list((config.hiraku_dir / "agent-logs").glob("*/meta.json"))
    assert logs
    assert json.loads(logs[0].read_text())["checks"]["precondition"]["passed"] is False
    unsupported = CliRunner().invoke(
        main,
        [
            "agent",
            "experiment",
            "implement",
            "--record",
            "baseline",
            "--commit",
        ],
    )
    assert unsupported.exit_code != 0
    assert "No such option '--commit'" in unsupported.output


def test_project_draft_overrides_bundled(tmp_path: Path, monkeypatch) -> None:
    config = HirakuConfig.init(tmp_path, install_skill=False)
    Record.create("task", type="task", config=config)
    custom = Action(
        id="draft",
        entity="experiment",
        effect="create",
        parent_types=["task"],
        to_status="draft",
        markdown="Project-specific experiment prompt",
    )
    custom.save(config.actions_dir / "experiment/draft/action.md")
    (custom.path.parent / "hooks.py").write_text(
        "raise RuntimeError('undeclared hook executed')\n", encoding="utf-8"
    )
    monkeypatch.chdir(tmp_path)

    def reply(provider, model, prompt, root, effort, **kwargs):
        assert "Project-specific experiment prompt" in prompt
        return '{"id": "custom"}'

    monkeypatch.setattr("hiraku.agent.provider.call_provider", reply)
    result = CliRunner().invoke(
        main, ["agent", "experiment", "draft", "--record", "task", "--json"]
    )
    assert result.exit_code == 0, result.output
    assert json.loads(result.stdout)["origin"] == "project"
    assert load_record("custom", config=config).status == "draft"


@pytest.mark.parametrize("provider", ["codex", "claude"])
def test_provider_streams_before_completion(
    tmp_path: Path, monkeypatch, provider: str
) -> None:
    config = HirakuConfig.init(tmp_path, install_skill=False)
    record = Record.create("task", type="task", config=config)
    marker = tmp_path / "received"
    script = tmp_path / "provider.py"
    progress = (
        {
            "type": "item.started",
            "item": {"type": "command_execution", "command": "inspect code"},
        }
        if provider == "codex"
        else {
            "type": "assistant",
            "message": {
                "content": [
                    {
                        "type": "tool_use",
                        "name": "Bash",
                        "input": {"command": "inspect code"},
                    }
                ]
            },
        }
    )
    final = (
        {
            "type": "item.completed",
            "item": {"type": "agent_message", "text": '{"id": "baseline"}'},
        }
        if provider == "codex"
        else {"type": "result", "result": '{"id": "baseline"}', "is_error": False}
    )
    script.write_text(
        "import json, pathlib, sys, time\n"
        "sys.stdin.read()\n"
        f"print({json.dumps(progress)!r}, flush=True)\n"
        f"marker = pathlib.Path({str(marker)!r})\n"
        "deadline = time.monotonic() + 5\n"
        "while not marker.exists() and time.monotonic() < deadline:\n"
        "    time.sleep(0.01)\n"
        "assert marker.exists(), 'feedback was buffered until completion'\n"
        f"print({json.dumps(final)!r}, flush=True)\n"
    )
    monkeypatch.setattr(
        "hiraku.agent.provider.provider_command",
        lambda *args: [sys.executable, str(script)],
    )
    context = ActionContext(
        config, Action(id="test", entity="experiment"), record, "", [], [], log=tmp_path
    )
    context.report = lambda *args, **kwargs: marker.touch()
    reply = _call_provider(provider, None, "brief", tmp_path, context=context)
    assert json.loads(reply) == {"id": "baseline"}
    assert "inspect code" in (tmp_path / "provider.log").read_text()


def test_declared_hooks_receive_record_and_runs(tmp_path: Path, monkeypatch) -> None:
    config = HirakuConfig.init(tmp_path, install_skill=False)
    Record.create("task", type="task", config=config)
    Record.create("baseline", parent_id="task", type="experiment", config=config)
    (tmp_path / "runs.csv").write_text("id,record_id\none,baseline\ntwo,task\n")
    custom = Action(
        id="inspect",
        entity="experiment",
        effect="transition",
        from_status="draft",
        to_status="implemented",
        hooks="hooks.py",
        markdown="Inspect it",
    )
    custom.save(config.actions_dir / "experiment/inspect/action.md")
    (custom.path.parent / "hooks.py").write_text(
        "from hiraku.agent.action import ConditionResult\n"
        "def precondition(context):\n"
        "    valid = (context.record.id == 'baseline' and "
        "[run.id for run in context.runs] == ['one'] and "
        "[record.id for record in context.related_records] == ['task'])\n"
        "    return ConditionResult.pass_() if valid else "
        "ConditionResult.fail('missing context')\n",
        encoding="utf-8",
    )
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(
        "hiraku.agent.provider.call_provider", lambda *args, **kwargs: "done"
    )
    result = CliRunner().invoke(
        main,
        [
            "agent",
            "experiment",
            "inspect",
            "--record",
            "baseline",
        ],
    )
    assert result.exit_code == 0, result.output
    assert load_record("baseline", config=config).status == "implemented"


def test_invalid_create_reply_keeps_parent_unchanged(
    tmp_path: Path, monkeypatch
) -> None:
    config = HirakuConfig.init(tmp_path, install_skill=False)
    Record.create("task", type="task", config=config)
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(
        "hiraku.agent.provider.call_provider",
        lambda *args, **kwargs: "not JSON",
    )
    result = CliRunner().invoke(main, ["agent", "experiment", "draft"])
    assert result.exit_code != 0
    assert "JSON object" in result.output
    assert load_record("task", config=config).status == "draft"
    assert [record.id for record in list_records(config=config)] == ["task"]


def test_provider_override_does_not_reuse_codex_defaults(
    tmp_path: Path, monkeypatch
) -> None:
    config = HirakuConfig.init(tmp_path, install_skill=False)
    config.agent_model = "codex-default"
    config.agent_reasoning_effort = "low"
    config.save()
    Record.create("task", type="task", config=config)
    monkeypatch.chdir(tmp_path)

    def reply(provider, model, prompt, root, effort, **kwargs):
        assert (provider, model, effort) == ("claude", None, None)
        return '{"id":"baseline","kind":"hypothesis","body":"Compare models"}'

    monkeypatch.setattr("hiraku.agent.provider.call_provider", reply)
    result = CliRunner().invoke(
        main,
        [
            "agent",
            "experiment",
            "draft",
            "--provider",
            "claude",
        ],
    )
    assert result.exit_code == 0, result.output
