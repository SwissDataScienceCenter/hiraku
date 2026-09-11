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

Releases are created through GitHub Actions. After the release notes are added
under `## [Unreleased]` in `CHANGELOG.md` and merged to a green `main`, use:

```text
Actions → Release → Run workflow → choose patch, minor, or major
```

The workflow checks out the current `main`, updates `pyproject.toml`,
`CHANGELOG.md`, and `uv.lock`, runs the tests, lint, documentation build, and
package build, then commits and pushes the release. Only after validation does
it create the matching annotated tag and GitHub Release with the wheel and
source distribution.

Do not normally create release commits or tags manually. If a workflow fails,
inspect the run before retrying; it should not create a release tag after a
failed validation.
