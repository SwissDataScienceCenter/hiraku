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

Release permissions are restricted to maintainers and administrators. Make sure the release notes are current under `## [Unreleased]` in `CHANGELOG.md`, then update a clean, up-to-date `main` branch:

```bash
git switch main
git pull --ff-only
python scripts/release.py patch
```

The script calculates the next version, checks the changelog, runs tests, linting, and the documentation build, then asks for confirmation. After confirmation, `bump-my-version` updates `pyproject.toml`, `CHANGELOG.md`, and `uv.lock`, creates the release commit and `vX.Y.Z` tag, and pushes both to `main`. The tag starts the release workflow, which builds the wheel and source distribution and attaches them to a new GitHub Release.

Use `--dry-run` to run checks without changing Git. Use `--skip-checks` only when checks have already been run separately.
