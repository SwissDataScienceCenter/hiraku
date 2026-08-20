from __future__ import annotations

import hashlib
import json
import os
import shutil
import sys
from pathlib import Path

from conftest import commit_all, run_process

PROJECT_ROOT = Path(__file__).parents[1]


def child_python_env() -> dict[str, str]:
    python_bin = str(Path(sys.executable).parent)
    return {"PATH": f"{python_bin}{os.pathsep}{os.environ.get('PATH', '')}"}


def install_example(repo: Path, name: str) -> None:
    shutil.copytree(PROJECT_ROOT / "examples" / name, repo / "examples" / name)


def configure_experiment(repo: Path, command: list[str], experiment_id: str, example: str) -> None:
    run_process([*command, "init"], repo)
    commit_all(repo, "initialize hiraku and example")
    run_process([*command, "exp", "create", experiment_id], repo)
    shutil.copyfile(
        repo / "examples" / example / "launch.yml",
        repo / "experiments" / experiment_id / "launch.yml",
    )
    commit_all(repo, f"configure {experiment_id}")


def test_smoke_example_full_lifecycle(repo: Path, hiraku_command: list[str]) -> None:
    install_example(repo, "smoke")
    configure_experiment(repo, hiraku_command, "smoke-demo", "smoke")
    env = child_python_env()

    run_process([*hiraku_command, "launch", "smoke-demo:baseline"], repo, env=env)
    run_process([*hiraku_command, "launch", "smoke-demo:candidate"], repo, env=env)
    result = run_process(
        [
            *hiraku_command,
            "runs",
            "smoke-demo",
            "--sort",
            "score",
            "--descending",
            "--json",
        ],
        repo,
    )

    rows = json.loads(result.stdout)
    assert [row["variant"] for row in rows] == ["candidate", "baseline"]
    assert [row["_rank"] for row in rows] == [1, 2]


def test_heart_disease_example_full_lifecycle(repo: Path, hiraku_command: list[str]) -> None:
    install_example(repo, "heart-disease")
    data_path = repo / "examples/heart-disease/data/processed.cleveland.data"
    assert len(data_path.read_text(encoding="utf-8").splitlines()) == 303
    assert (
        hashlib.sha256(data_path.read_bytes()).hexdigest()
        == "a74b7efa387bc9d108d7d0115d831fe9b414b29ae7124f331b622b4efa0427c8"
    )
    configure_experiment(repo, hiraku_command, "heart-disease-models", "heart-disease")
    env = child_python_env()

    run_process([*hiraku_command, "launch", "heart-disease-models:logistic"], repo, env=env)
    run_process(
        [*hiraku_command, "launch", "heart-disease-models:random-forest"],
        repo,
        env=env,
    )
    result = run_process(
        [
            *hiraku_command,
            "runs",
            "heart-disease-models",
            "--sort",
            "roc_auc_mean",
            "--descending",
            "--json",
        ],
        repo,
    )

    rows = json.loads(result.stdout)
    assert len(rows) == 2
    assert {row["model"] for row in rows} == {"logistic", "random-forest"}
    assert [row["_rank"] for row in rows] == [1, 2]
    assert all(0.0 <= float(row["roc_auc_mean"]) <= 1.0 for row in rows)
