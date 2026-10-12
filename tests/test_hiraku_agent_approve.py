"""Tests for `hiraku-agent approve` with several nodes or --all."""

from __future__ import annotations

from pathlib import Path

import pytest
from click.testing import CliRunner

from hiraku.config import HirakuConfig
from hiraku.record import Record
from hiraku_agent.cli import main
from hiraku_agent.gates import approve
from hiraku_agent.node import create_node, load_node


@pytest.fixture
def config(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> HirakuConfig:
    selected = HirakuConfig.init(tmp_path)
    monkeypatch.chdir(tmp_path)
    task = Record.create(
        "matcher", parent_id=None, type="task", config=selected, goal="g"
    )
    for node_id in ("a", "b", "c"):
        create_node(
            node_id,
            task=task,
            kind="hypothesis",
            expectation=f"{node_id} wins",
            config=selected,
        )
    implemented = create_node(
        "impl", task=task, kind="hypothesis", expectation="impl wins", config=selected
    )
    implemented.commit = "a" * 40
    implemented.save()
    create_node(
        "dropped", task=task, kind="hypothesis", abandoned=True, config=selected
    )
    approve(load_node("c", task="matcher", config=selected), by="earlier")
    return selected


def _approved(config: HirakuConfig, node_id: str) -> str | None:
    return load_node(node_id, task="matcher", config=config).approved_by


def test_approves_several_named_nodes(config: HirakuConfig) -> None:
    result = CliRunner().invoke(
        main, ["approve", "a", "b", "c", "--task", "matcher", "--by", "qz"]
    )
    assert result.exit_code == 0, result.output
    assert _approved(config, "a") == "qz" and _approved(config, "b") == "qz"
    assert _approved(config, "c") == "earlier"  # re-approving keeps the original
    assert "(already approved)" in result.output


def test_all_lists_waiting_nodes_and_asks_first(config: HirakuConfig) -> None:
    runner = CliRunner()
    aborted = runner.invoke(
        main, ["approve", "--all", "--task", "matcher", "--by", "qz"], input="n\n"
    )
    assert aborted.exit_code != 0
    assert "a [proposed] a wins" in aborted.output
    assert "impl [implemented] impl wins" in aborted.output
    assert "dropped" not in aborted.output and " c " not in aborted.output
    assert _approved(config, "a") is None

    done = runner.invoke(
        main, ["approve", "--all", "--task", "matcher", "--by", "qz", "--yes"]
    )
    assert done.exit_code == 0, done.output
    for node_id in ("a", "b", "impl"):
        assert _approved(config, node_id) == "qz"
    assert _approved(config, "dropped") is None

    again = runner.invoke(
        main, ["approve", "--all", "--task", "matcher", "--by", "qz", "--yes"]
    )
    assert "Nothing in matcher is waiting for approval" in again.output


def test_names_and_all_are_exclusive_and_abandoned_is_refused(
    config: HirakuConfig,
) -> None:
    runner = CliRunner()
    both = runner.invoke(main, ["approve", "a", "--all", "--by", "qz"])
    assert both.exit_code != 0
    dropped = runner.invoke(
        main, ["approve", "a", "dropped", "--task", "matcher", "--by", "qz"]
    )
    assert dropped.exit_code != 0
    assert "abandoned" in dropped.output
    assert _approved(config, "a") is None  # nothing approved when one is refused
