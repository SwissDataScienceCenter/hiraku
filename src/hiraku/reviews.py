from __future__ import annotations

import argparse
import json
import re
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from hiraku.config import Project
from hiraku.errors import HirakuError, ValidationError
from hiraku.git import (
    branch_published,
    branch_upstream_remote,
    current_branch,
    push_branch,
    remote_branch_commit,
    remote_default_branch,
    remote_names,
    remote_url,
    resolve_commit,
)
from hiraku.records import TaskRecord, read_task, resolve_task, write_task
from hiraku.util import json_dump, render_table


@dataclass(frozen=True)
class Review:
    id: str
    state: str
    url: str
    head: str
    base: str


def add_review_parser(commands: argparse._SubParsersAction[argparse.ArgumentParser]) -> None:
    review = commands.add_parser("review", help="Create or update a pull/merge request")
    subcommands = review.add_subparsers(dest="review_command", required=True)
    create = subcommands.add_parser("create", help="Create or update a pull/merge request")
    create.add_argument("task_id", nargs="?")
    create.add_argument("--base")
    create.add_argument("--remote")
    create.add_argument("--review")
    create.add_argument("--push", action="store_true")
    create.add_argument("--json", action="store_true")


def _run(argv: list[str]) -> str:
    try:
        result = subprocess.run(argv, text=True, capture_output=True, check=False)
    except FileNotFoundError as exc:
        raise HirakuError(
            f"Required provider CLI {argv[0]!r} is not installed or not on PATH."
        ) from exc
    if result.returncode:
        detail = result.stderr.strip() or result.stdout.strip() or "unknown provider error"
        raise HirakuError(f"{' '.join(argv[:2])} failed: {detail}")
    return result.stdout.strip()


def _json(argv: list[str]) -> Any:
    output = _run(argv)
    try:
        return json.loads(output or "null")
    except json.JSONDecodeError as exc:
        raise HirakuError(f"Provider CLI returned invalid JSON for {' '.join(argv[:2])}.") from exc


def repository_name(url: str) -> str:
    value = url.strip().removesuffix("/")
    scp_match = re.match(r"^[^/@:]+@([^:]+):(.+)$", value)
    if scp_match:
        host, path = scp_match.groups()
        path = path.strip("/").removesuffix(".git")
        return f"{host}/{path}"
    else:
        parsed = urlparse(value)
        path = parsed.path if parsed.scheme else value
    path = path.strip("/").removesuffix(".git")
    if "/" not in path:
        raise ValidationError(f"Cannot derive provider repository from remote URL {url!r}.")
    return f"{parsed.hostname}/{path}" if parsed.scheme and parsed.hostname else path


def select_remote(project: Project, task: TaskRecord, option: str | None) -> str:
    names = remote_names(project.root)
    upstream = branch_upstream_remote(project.root, task.branch)
    candidates = [option, project.review_remote, upstream, "origin" if "origin" in names else None]
    candidates.extend(names if len(names) == 1 else [])
    selected = next((value for value in candidates if value), None)
    if not selected or selected not in names:
        raise HirakuError(
            "Could not select a Git remote; configure review_remote or pass --remote."
        )
    return selected


class Provider:
    name: str

    def authenticate(self) -> None:
        raise NotImplementedError

    def find(self, repository: str, head: str, base: str) -> list[Review]:
        raise NotImplementedError

    def get(self, repository: str, identifier: str) -> Review:
        raise NotImplementedError

    def create(self, repository: str, head: str, base: str, title: str, body_file: Path) -> Review:
        raise NotImplementedError

    def update(self, repository: str, identifier: str, title: str, body_file: Path) -> Review:
        raise NotImplementedError


def _github_review(value: dict[str, Any]) -> Review:
    return Review(
        id=str(value.get("number", "")),
        state=str(value.get("state", "")),
        url=str(value.get("url", "")),
        head=str(value.get("headRefName", "")),
        base=str(value.get("baseRefName", "")),
    )


class GitHubProvider(Provider):
    name = "github"

    def authenticate(self) -> None:
        _run(["gh", "auth", "status"])

    def find(self, repository: str, head: str, base: str) -> list[Review]:
        values = _json(
            [
                "gh",
                "pr",
                "list",
                "--repo",
                repository,
                "--head",
                head,
                "--base",
                base,
                "--state",
                "all",
                "--json",
                "number,state,url,headRefName,baseRefName",
            ]
        )
        return [_github_review(value) for value in values]

    def get(self, repository: str, identifier: str) -> Review:
        value = _json(
            [
                "gh",
                "pr",
                "view",
                identifier,
                "--repo",
                repository,
                "--json",
                "number,state,url,headRefName,baseRefName",
            ]
        )
        return _github_review(value)

    def create(self, repository: str, head: str, base: str, title: str, body_file: Path) -> Review:
        url = _run(
            [
                "gh",
                "pr",
                "create",
                "--repo",
                repository,
                "--head",
                head,
                "--base",
                base,
                "--title",
                title,
                "--body-file",
                str(body_file),
            ]
        ).splitlines()[-1]
        match = re.search(r"/(?:pull)/(\d+)(?:$|[/?#])", url)
        if not match:
            raise HirakuError("GitHub did not return a recognizable pull-request URL.")
        return self.get(repository, match.group(1))

    def update(self, repository: str, identifier: str, title: str, body_file: Path) -> Review:
        _run(
            [
                "gh",
                "pr",
                "edit",
                identifier,
                "--repo",
                repository,
                "--title",
                title,
                "--body-file",
                str(body_file),
            ]
        )
        return self.get(repository, identifier)


def _gitlab_review(value: dict[str, Any]) -> Review:
    return Review(
        id=str(value.get("iid", value.get("id", ""))),
        state=str(value.get("state", "")),
        url=str(value.get("web_url", value.get("webUrl", value.get("url", "")))),
        head=str(value.get("source_branch", value.get("sourceBranch", ""))),
        base=str(value.get("target_branch", value.get("targetBranch", ""))),
    )


class GitLabProvider(Provider):
    name = "gitlab"

    def authenticate(self) -> None:
        _run(["glab", "auth", "status"])

    def find(self, repository: str, head: str, base: str) -> list[Review]:
        values = _json(
            [
                "glab",
                "mr",
                "list",
                "--repo",
                repository,
                "--all",
                "--source-branch",
                head,
                "--target-branch",
                base,
                "--output",
                "json",
            ]
        )
        return [_gitlab_review(value) for value in values]

    def get(self, repository: str, identifier: str) -> Review:
        return _gitlab_review(
            _json(
                [
                    "glab",
                    "mr",
                    "view",
                    identifier,
                    "--repo",
                    repository,
                    "--output",
                    "json",
                ]
            )
        )

    def create(self, repository: str, head: str, base: str, title: str, body_file: Path) -> Review:
        output = _run(
            [
                "glab",
                "mr",
                "create",
                "--repo",
                repository,
                "--source-branch",
                head,
                "--target-branch",
                base,
                "--title",
                title,
                "--description-file",
                str(body_file),
                "--yes",
            ]
        )
        match = re.search(r"/-/merge_requests/(\d+)(?:$|[/?#])", output)
        if not match:
            raise HirakuError("GitLab did not return a recognizable merge-request URL.")
        return self.get(repository, match.group(1))

    def update(self, repository: str, identifier: str, title: str, body_file: Path) -> Review:
        _run(
            [
                "glab",
                "mr",
                "update",
                identifier,
                "--repo",
                repository,
                "--title",
                title,
                "--description-file",
                str(body_file),
            ]
        )
        return self.get(repository, identifier)


def provider_for(project: Project) -> Provider:
    if project.review_provider == "github":
        return GitHubProvider()
    if project.review_provider == "gitlab":
        return GitLabProvider()
    raise ValidationError(
        "Review commands require review_provider: github or review_provider: gitlab."
    )


def _recorded_id(task: TaskRecord, provider: Provider) -> str:
    if provider.name == "github":
        if task.mr_id:
            raise ValidationError("Task field mr_id cannot be used with review_provider: github.")
        return task.pr_id
    if task.pr_id:
        raise ValidationError("Task field pr_id cannot be used with review_provider: gitlab.")
    return task.mr_id


def _store_id(task: TaskRecord, provider: Provider, value: str) -> None:
    if provider.name == "github":
        task.pr_id = value
    else:
        task.mr_id = value
    write_task(task)


def _output(
    args: argparse.Namespace,
    provider: Provider,
    action: str,
    review: Review,
    remote: str,
    head_sha: str,
    base_sha: str,
) -> None:
    value = {
        "provider": provider.name,
        "action": action,
        "id": review.id,
        "url": review.url,
        "state": review.state,
        "remote": remote,
        "base": review.base,
        "head": review.head,
        "base_sha": base_sha,
        "head_sha": head_sha,
    }
    print(json_dump(value) if args.json else f"{action.title()} {review.url}")


def review_create(project: Project, args: argparse.Namespace) -> int:
    task_id = resolve_task(project, args.task_id)
    task = read_task(project, task_id)
    if not task.summary.strip():
        raise ValidationError(f"Task {task_id!r} requires a non-empty summary for review.")
    provider = provider_for(project)
    recorded = _recorded_id(task, provider)
    if args.review and recorded and args.review != recorded:
        raise ValidationError(
            f"--review {args.review!r} conflicts with the recorded identifier {recorded!r}."
        )
    selected_id = args.review or recorded
    remote = select_remote(project, task, args.remote)
    selected_url = remote_url(project.root, remote)
    repository = repository_name(selected_url) if provider.name == "github" else selected_url
    base = args.base or remote_default_branch(project.root, remote)
    head = task.branch
    head_sha = resolve_commit(project.root, head)
    base_sha = remote_branch_commit(project.root, remote, base)

    if not args.json:
        print(task.path.read_text(encoding="utf-8").rstrip())
    published = branch_published(project.root, remote, head)
    if not published and not args.push:
        instruction = f"git push {remote} {head}:refs/heads/{head}"
        if args.json:
            print(
                json_dump(
                    {
                        "provider": provider.name,
                        "action": "preview",
                        "reason": "unpublished_head",
                        "remote": remote,
                        "base": base,
                        "head": head,
                        "base_sha": base_sha,
                        "head_sha": head_sha,
                        "instruction": instruction,
                    }
                )
            )
        else:
            print(
                f"Task branch {head!r} is not published. Publish with:\n"
                f"  {instruction}\nThen rerun the review command, or pass --push."
            )
        return 0

    provider.authenticate()
    if selected_id:
        existing = provider.get(repository, selected_id)
        if existing.head and existing.head != head:
            raise ValidationError(
                f"Review {selected_id} has head {existing.head!r}, expected {head!r}."
            )
        if existing.base and existing.base != base:
            raise ValidationError(
                f"Review {selected_id} has base {existing.base!r}, expected {base!r}."
            )
        if args.push:
            push_branch(project.root, remote, head)
        updated = provider.update(repository, selected_id, task.summary, task.path)
        _output(args, provider, "updated", updated, remote, head_sha, base_sha)
        return 0

    matches = provider.find(repository, head, base)
    if matches:
        rows = [{"id": value.id, "state": value.state, "url": value.url} for value in matches]
        if args.json:
            print(
                json_dump(
                    {
                        "provider": provider.name,
                        "action": "selection_required",
                        "base": base,
                        "head": head,
                        "matches": rows,
                        "instruction": "Rerun with --review <id>.",
                    }
                )
            )
        else:
            print("Matching reviews already exist; select one with --review <id>:")
            print(render_table(rows, ["id", "state", "url"]))
        return 0
    if current_branch(project.root) != head:
        raise HirakuError(
            f"First-time review creation must run on task branch {head!r}. "
            f"Switch with: git switch {head}"
        )
    if args.push or not published:
        push_branch(project.root, remote, head)
    created = provider.create(repository, head, base, task.summary, task.path)
    _store_id(task, provider, created.id)
    updated = provider.update(repository, created.id, task.summary, task.path)
    _output(args, provider, "created", updated, remote, head_sha, base_sha)
    return 0
