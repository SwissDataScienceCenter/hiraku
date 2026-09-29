from __future__ import annotations

import subprocess
from pathlib import Path

import click

ROOT = Path(__file__).resolve().parents[1]


def run(*args: str) -> None:
    click.echo(f"$ {' '.join(args)}")
    subprocess.run(args, cwd=ROOT, check=True)


def output(*args: str) -> str:
    result = subprocess.run(args, cwd=ROOT, capture_output=True, text=True, check=True)
    return result.stdout.strip()


def working_tree_is_clean() -> bool:
    result = subprocess.run(
        ["git", "status", "--porcelain"],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=True,
    )
    return not result.stdout.strip()


@click.command()
@click.argument(
    "release_type",
    type=click.Choice(["major", "minor", "patch"], case_sensitive=True),
)
@click.option(
    "--skip-checks",
    is_flag=True,
    help="Skip tests, linting, documentation, and changelog checks.",
)
@click.option(
    "--dry-run",
    is_flag=True,
    help="Show the next version and run checks without changing Git state.",
)
def main(release_type: str, skip_checks: bool, dry_run: bool) -> None:
    """Create and push a release from the main branch."""
    branch = output("git", "branch", "--show-current")
    if branch != "main":
        raise click.ClickException(
            f"Releases must be created from main, not {branch or 'detached HEAD'}."
        )
    if not working_tree_is_clean():
        raise click.ClickException("Working tree is not clean")

    try:
        new_version = output(
            "uv",
            "run",
            "bump-my-version",
            "show",
            "new_version",
            "--increment",
            release_type,
        )
    except subprocess.CalledProcessError as exc:
        raise click.ClickException("Could not determine the next version") from exc

    click.echo(f"Preparing release v{new_version} ({release_type})")
    if not skip_checks:
        run("uv", "run", "python", "scripts/check_changelog.py")
        run("uv", "run", "pytest")
        run("uv", "run", "ruff", "check", ".")
        run("uv", "run", "zensical", "build", "--clean")

    if dry_run:
        click.echo("Dry run complete; no files, commits, or tags were changed.")
        return

    if not click.confirm(
        f"Create and push release v{new_version} from main?", default=False
    ):
        raise click.Abort()

    run("uv", "run", "bump-my-version", "bump", release_type)
    run("git", "push", "origin", "main", "--follow-tags")
    click.echo(f"Release v{new_version} pushed successfully.")


if __name__ == "__main__":
    main()
