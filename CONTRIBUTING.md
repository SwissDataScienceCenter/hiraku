# Contributing to Hiraku

## Feature requests and bug reports

Submit issues and feature requests through the [GitHub issue tracker](https://github.com/SwissDataScienceCenter/hiraku/issues). Bug reports should include the command, a minimal reproduction, Python version, and operating system.

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

1. Make sure `## [Unreleased]` in `CHANGELOG.md` has at least one entry. In
   GitHub Actions, run **Prepare release** from `main` and select `patch`,
   `minor`, or `major`. The workflow bumps the version, updates the lockfile and
   changelog, and opens a PR. [Prepare release](https://github.com/SwissDataScienceCenter/hiraku/actions/workflows/prepare-release.yml)
2. Select **Approve workflows to run** on the PR to start CI. Review and merge
   the PR once the checks pass.
3. Tag the merged commit using the version and commit SHA shown by GitHub.

   ```bash
   git fetch origin main
   git tag vX.Y.Z <merge-commit-sha>
   git push origin vX.Y.Z
   ```

The Release workflow checks the tag against the project version and confirms the
tagged commit is on `main`. It builds the distributions, publishes them to PyPI,
and attaches the same files to the GitHub release.
