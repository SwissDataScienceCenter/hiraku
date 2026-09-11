# Contributing to Hiraku

## Issues and user stories

Submit issues and feature requests through the [GitHub issue tracker](https://github.com/SwissDataScienceCenter/hiraku/issues). Bug reports should include the command, a minimal reproduction, Python version, and operating system.

Submit user stories through the [issue tracker](https://github.com/SwissDataScienceCenter/hiraku/issues/new?labels=%22user%20story%22). Describe the problem, intended workflow, desired outcome, and relevant context.

## Development setup

Hiraku uses Python 3.11+.

```bash
git clone https://github.com/SwissDataScienceCenter/hiraku.git
cd hiraku
uv sync --all-extras
uv run hiraku --help
```

Install the pre-commit hooks:

```bash
uv run pre-commit install
```

Run the hooks manually across the repository with:

```bash
uv run pre-commit run --all-files
```

Install optional tracking integrations when needed:

```bash
uv sync --extra mlflow
uv sync --extra wandb

# or install everything
uv sync --all-extras
```

## Development workflow

Run these checks before opening a pull request:

```bash
uv run pytest
uv run ruff check src tests
uv run zensical build --clean
```

Tests should not require network access or W&B/MLflow credentials.

Keep public models and CLI behavior explicit. Add tests for new behavior and
update the relevant documentation when a public interface changes.

### Documentation and examples

Preview the documentation locally:

```bash
uv run zensical serve
```

Keep onboarding instructions short. Examples should have simple commands and
an end-to-end test under `tests/examples/`.

### Commits and pull requests

Use a feature branch and focused commits. [Conventional Commit](https://www.conventionalcommits.org/) prefixes such as `feat:`, `fix:`, `docs:`, `test:`, and `refactor:` are preferred. Pull requests should describe the user-visible change, tests run, and compatibility or configuration changes.

## Release a new version

Make sure the release notes are current under `## [Unreleased]` in
`CHANGELOG.md` and the working tree is clean. From an up-to-date `main`, choose
the version increment and prepare a release branch:

```bash
git switch main
git pull --ff-only

BUMP=minor  # patch, minor, or major
VERSION=$(uv run bump-my-version show new_version --increment "$BUMP")
git switch -c "release/v${VERSION}"
uv run bump-my-version bump "$BUMP" --commit --no-tag
git push -u origin "release/v${VERSION}"
```

The bump command updates `pyproject.toml` and `CHANGELOG.md`, regenerates
`uv.lock`, and commits all three files. Open a PR from the release branch and
merge it through the normal protected path. Do not tag the release branch.

After CI passes on the merged `main` commit, update your local branch and tag
that exact commit:

```bash
git switch main
git pull --ff-only
git show --stat --oneline HEAD  # confirm this is the merged release commit

VERSION=$(python -c 'import tomllib; print(tomllib.load(open("pyproject.toml", "rb"))["project"]["version"])')
git tag -a "v${VERSION}" -m "Release v${VERSION}"
git push origin "v${VERSION}"
```

Pushing the tag starts the release workflow. It verifies that the tag identifies
the release version commit on `main` and matches the package and lockfile
versions. It then reruns the tests, lint, and documentation build before
building the wheel and source distribution from the tagged commit. If
validation succeeds, it verifies the artifact checksums and creates the GitHub
Release.
