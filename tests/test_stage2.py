from __future__ import annotations

import json
import os
import stat
from pathlib import Path

import yaml
from conftest import hiraku, run


def update_task_summary(repository: Path, task_id: str, summary: str) -> None:
    path = repository / f"tasks/{task_id}/task.md"
    text = path.read_text(encoding="utf-8")
    path.write_text(text.replace('summary: ""', f'summary: "{summary}"'), encoding="utf-8")


def test_experiment_branch_creation_and_task_inference(repository: Path) -> None:
    hiraku(repository, "task", "create", "baseline")
    run(repository, "git", "switch", "hiraku/task/baseline")
    run(repository, "git", "add", ".")
    run(repository, "git", "commit", "-m", "create task")

    created = hiraku(
        repository,
        "exp",
        "create",
        "baseline:parallel",
        "--branch",
        "--summary",
        "Parallel idea",
    )
    branch = "hiraku/experiment/baseline/parallel"
    assert f"Experiment branch '{branch}' created" in created.stdout
    assert run(repository, "git", "branch", "--show-current").stdout.strip() == branch
    task = json.loads(hiraku(repository, "task", "--json").stdout)
    assert task["id"] == "baseline"
    experiment = yaml.safe_load(
        (repository / "tasks/baseline/experiments/parallel.yml").read_text(encoding="utf-8")
    )
    assert experiment["branch"] == branch


def test_experiment_branch_existing_ref_is_not_reset(repository: Path) -> None:
    hiraku(repository, "task", "create", "baseline")
    run(repository, "git", "switch", "hiraku/task/baseline")
    run(repository, "git", "add", ".")
    run(repository, "git", "commit", "-m", "create task")
    branch = "hiraku/experiment/baseline/trial"
    run(repository, "git", "branch", branch)
    expected = run(repository, "git", "rev-parse", branch).stdout.strip()

    result = hiraku(repository, "exp", "create", "baseline:trial", "--branch")
    assert "already existed and was not reset" in result.stdout
    assert run(repository, "git", "rev-parse", branch).stdout.strip() == expected


def test_branch_flag_is_boolean_and_from_requires_it(repository: Path) -> None:
    hiraku(repository, "task", "create", "baseline")
    invalid = hiraku(
        repository,
        "exp",
        "create",
        "baseline:trial",
        "--from",
        "HEAD",
        check=False,
    )
    assert invalid.returncode == 2
    assert "--from requires --branch" in invalid.stderr
    arbitrary = hiraku(
        repository,
        "task",
        "create",
        "custom",
        "--branch",
        "other",
        check=False,
    )
    assert arbitrary.returncode == 2
    assert "unrecognized arguments: --branch" in arbitrary.stderr


def test_lineage_and_task_branch_containment(repository: Path) -> None:
    hiraku(repository, "task", "create", "baseline")
    run(repository, "git", "switch", "hiraku/task/baseline")
    run(repository, "git", "add", ".")
    run(repository, "git", "commit", "-m", "create task")
    hiraku(repository, "exp", "create", "baseline:first")
    run(repository, "git", "add", ".")
    run(repository, "git", "commit", "-m", "first experiment")
    hiraku(repository, "exp", "set", "baseline:first", "--commit", "HEAD")
    first = run(repository, "git", "rev-parse", "HEAD").stdout.strip()
    hiraku(repository, "exp", "create", "baseline:second")
    run(repository, "git", "add", ".")
    run(repository, "git", "commit", "-m", "second experiment")
    hiraku(repository, "exp", "set", "baseline:second", "--commit", "HEAD")
    values = json.loads(hiraku(repository, "exp", "list", "baseline", "--json").stdout)
    second = next(value for value in values if value["id"] == "second")
    assert second["parent_experiments"] == ["first"]
    assert second["contained_in_task_branch"] is True
    assert first in second["git_parents"]


def test_github_review_creation_records_pr_id(repository: Path, tmp_path: Path) -> None:
    remote = tmp_path / "remote.git"
    run(tmp_path, "git", "init", "--bare", str(remote))
    run(repository, "git", "remote", "add", "origin", str(remote))
    run(repository, "git", "push", "-u", "origin", "main")
    run(remote, "git", "symbolic-ref", "HEAD", "refs/heads/main")

    config_path = repository / ".hiraku.yml"
    config = yaml.safe_load(config_path.read_text(encoding="utf-8"))
    config.update({"review_provider": "github", "review_remote": "origin"})
    config_path.write_text(yaml.safe_dump(config, sort_keys=False), encoding="utf-8")
    hiraku(repository, "task", "create", "baseline")
    run(repository, "git", "switch", "hiraku/task/baseline")
    update_task_summary(repository, "baseline", "Baseline review")
    run(repository, "git", "add", ".")
    run(repository, "git", "commit", "-m", "create task")
    run(repository, "git", "push", "-u", "origin", "hiraku/task/baseline")

    binary = tmp_path / "bin"
    binary.mkdir()
    log = tmp_path / "gh.log"
    body = tmp_path / "body.md"
    fake = binary / "gh"
    fake.write_text(
        """#!/usr/bin/env python3
import json
import os
import pathlib
import sys

args = sys.argv[1:]
with pathlib.Path(os.environ["GH_LOG"]).open("a", encoding="utf-8") as stream:
    stream.write(json.dumps(args) + "\\n")
if args[:2] == ["auth", "status"]:
    raise SystemExit(0)
if args[:2] == ["pr", "list"]:
    print("[]")
elif args[:2] == ["pr", "create"]:
    print("https://github.example/acme/repo/pull/7")
elif args[:2] == ["pr", "view"]:
    print(json.dumps({
        "number": 7,
        "state": "OPEN",
        "url": "https://github.example/acme/repo/pull/7",
        "headRefName": "hiraku/task/baseline",
        "baseRefName": "main",
    }))
elif args[:2] == ["pr", "edit"]:
    source = pathlib.Path(args[args.index("--body-file") + 1])
    destination = pathlib.Path(os.environ["GH_BODY"])
    destination.write_text(source.read_text(encoding="utf-8"), encoding="utf-8")
else:
    raise SystemExit(3)
""",
        encoding="utf-8",
    )
    fake.chmod(fake.stat().st_mode | stat.S_IXUSR)
    env = {
        "PATH": f"{binary}{os.pathsep}{os.environ['PATH']}",
        "GH_LOG": str(log),
        "GH_BODY": str(body),
    }
    result = run(
        repository,
        os.sys.executable,
        "-m",
        "hiraku",
        "review",
        "create",
        "baseline",
        "--json",
        env=env,
    )
    value = json.loads(result.stdout)
    assert value["action"] == "created"
    assert value["id"] == "7"
    task_data = yaml.safe_load(
        (repository / "tasks/baseline/task.md").read_text(encoding="utf-8").split("---")[1]
    )
    assert task_data["pr_id"] == "7"
    assert "pr_id: '7'" in body.read_text(encoding="utf-8")
    commands = map(json.loads, log.read_text().splitlines())
    assert any(command[:2] == ["pr", "edit"] for command in commands)
    repeated = run(
        repository,
        os.sys.executable,
        "-m",
        "hiraku",
        "review",
        "create",
        "baseline",
        "--json",
        env=env,
    )
    assert json.loads(repeated.stdout)["action"] == "updated"


def test_gitlab_review_creation_records_mr_id(repository: Path, tmp_path: Path) -> None:
    remote = tmp_path / "gitlab-remote.git"
    run(tmp_path, "git", "init", "--bare", str(remote))
    run(repository, "git", "remote", "add", "origin", str(remote))
    run(repository, "git", "push", "-u", "origin", "main")
    run(remote, "git", "symbolic-ref", "HEAD", "refs/heads/main")

    config_path = repository / ".hiraku.yml"
    config = yaml.safe_load(config_path.read_text(encoding="utf-8"))
    config.update({"review_provider": "gitlab", "review_remote": "origin"})
    config_path.write_text(yaml.safe_dump(config, sort_keys=False), encoding="utf-8")
    hiraku(repository, "task", "create", "baseline")
    run(repository, "git", "switch", "hiraku/task/baseline")
    update_task_summary(repository, "baseline", "Baseline merge request")
    run(repository, "git", "add", ".")
    run(repository, "git", "commit", "-m", "create task")
    run(repository, "git", "push", "-u", "origin", "hiraku/task/baseline")

    binary = tmp_path / "gitlab-bin"
    binary.mkdir()
    log = tmp_path / "glab.log"
    body = tmp_path / "gitlab-body.md"
    fake = binary / "glab"
    fake.write_text(
        """#!/usr/bin/env python3
import json
import os
import pathlib
import sys

args = sys.argv[1:]
with pathlib.Path(os.environ["GLAB_LOG"]).open("a", encoding="utf-8") as stream:
    stream.write(json.dumps(args) + "\\n")
if args[:2] == ["auth", "status"]:
    raise SystemExit(0)
if args[:2] == ["mr", "list"]:
    print("[]")
elif args[:2] == ["mr", "create"]:
    print("https://gitlab.example/acme/repo/-/merge_requests/11")
elif args[:2] == ["mr", "view"]:
    print(json.dumps({
        "iid": 11,
        "state": "opened",
        "web_url": "https://gitlab.example/acme/repo/-/merge_requests/11",
        "source_branch": "hiraku/task/baseline",
        "target_branch": "main",
    }))
elif args[:2] == ["mr", "update"]:
    source = pathlib.Path(args[args.index("--description-file") + 1])
    destination = pathlib.Path(os.environ["GLAB_BODY"])
    destination.write_text(source.read_text(encoding="utf-8"), encoding="utf-8")
else:
    raise SystemExit(3)
""",
        encoding="utf-8",
    )
    fake.chmod(fake.stat().st_mode | stat.S_IXUSR)
    env = {
        "PATH": f"{binary}{os.pathsep}{os.environ['PATH']}",
        "GLAB_LOG": str(log),
        "GLAB_BODY": str(body),
    }
    result = run(
        repository,
        os.sys.executable,
        "-m",
        "hiraku",
        "review",
        "create",
        "baseline",
        "--json",
        env=env,
    )
    value = json.loads(result.stdout)
    assert value["action"] == "created"
    assert value["id"] == "11"
    task_data = yaml.safe_load(
        (repository / "tasks/baseline/task.md").read_text(encoding="utf-8").split("---")[1]
    )
    assert task_data["mr_id"] == "11"
    assert "mr_id: '11'" in body.read_text(encoding="utf-8")
    commands = map(json.loads, log.read_text().splitlines())
    assert any(command[:2] == ["mr", "update"] for command in commands)


def test_review_rejects_provider_mismatched_identifier(repository: Path) -> None:
    config_path = repository / ".hiraku.yml"
    config = yaml.safe_load(config_path.read_text(encoding="utf-8"))
    config["review_provider"] = "github"
    config_path.write_text(yaml.safe_dump(config, sort_keys=False), encoding="utf-8")
    hiraku(repository, "task", "create", "baseline")
    update_task_summary(repository, "baseline", "Baseline")
    task_path = repository / "tasks/baseline/task.md"
    task_path.write_text(
        task_path.read_text(encoding="utf-8").replace(
            'branch: "hiraku/task/baseline"',
            'branch: "hiraku/task/baseline"\nmr_id: "4"',
        ),
        encoding="utf-8",
    )
    result = hiraku(repository, "review", "create", "baseline", check=False)
    assert result.returncode == 2
    assert "mr_id cannot be used with review_provider: github" in result.stderr
