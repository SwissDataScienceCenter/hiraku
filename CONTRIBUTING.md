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
pre-commit install
```

Run the hooks manually across the repository with:

```bash
pre-commit run --all-files
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
pytest -n auto
ruff check src tests
zensical build --clean --strict
```

Pytest uses parallel workers, capped at four to limit memory use.
Write tests as pytest functions; do not use `unittest.TestCase`. Tests should
not require network access or W&B/MLflow credentials.

Remove Python cache debris with `uvx pyclean . --debris`.

Keep public models and CLI behavior explicit. Add tests for new behavior and
update the relevant documentation when a public interface changes.

### Documentation and examples

Preview the documentation locally:

```bash
zensical serve
```

Keep onboarding instructions short. Examples should have simple commands and
an end-to-end test under `tests/examples/`.

### Commits and pull requests

Use a feature branch and focused commits. [Conventional Commit](https://www.conventionalcommits.org/) prefixes such as `feat:`, `fix:`, `docs:`, `test:`, and `refactor:` are preferred. Pull requests should describe the user-visible change, tests run, and compatibility or configuration changes.

## Release a new version

Release permissions are restricted to maintainers and administrators.

The `--bump` option supports the following common version components: `major`, `minor`, and `patch`.

From an up-to-date `main` branch, create a temporary branch and let `uv` update the project version and lockfile:

```bash
git switch main
git pull --ff-only
git switch -c release/prepare
uv version --bump patch
VERSION=$(uv version --short)
git branch -m "release/v${VERSION}"
uv run --no-sync python scripts/update_changelog.py
```

The script adds the dated version heading below `## [Unreleased]` with the unreleased notes then fall under the new version heading.

Commit and push the release branch:

```bash
git add pyproject.toml uv.lock CHANGELOG.md
git commit -m "chore(release): prepare v${VERSION}"
git push -u origin HEAD
```

Open a pull request from `release/vX.Y.Z` to `main`; the PR CI runs the tests, lint, and docs checks.

After the PR merges, tag its merge commit using the actual version and merge commit SHA shown by GitHub:

```bash
git tag vX.Y.Z <merge-commit-sha>
git push origin vX.Y.Z
```

CI verifies that the tag matches the project version and points to a commit on `main`, builds the distributions, publishes them to PyPI, then attaches those same files to the GitHub Release.

Use `uv version --bump minor` or `uv version --bump major` for other release types. `uv` also supports `stable`, `alpha`, `beta`, `rc`, `post`, and `dev` bumps.
