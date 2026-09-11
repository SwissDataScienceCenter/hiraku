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

Releases use `bump-my-version` and must be created from `main`.

1. Add release notes under `## [Unreleased]` in `CHANGELOG.md`, including issue
   or pull request links. Run the checks above, commit the changelog, and merge
   it to `main`. Do not add the final version heading manually.
2. Update `main` and confirm that the working tree is clean:

   ```bash
   git switch main
   git pull --ff-only origin main
   git status --short
   ```

3. Choose `patch` for fixes, `minor` for compatible features, or `major` for
   breaking changes. Preview the bump first:

   ```bash
   uv run bump-my-version bump patch --dry-run
   ```

4. If the preview is correct, create the release commit and tag:

   ```bash
   uv run bump-my-version bump patch
   ```

   This updates `pyproject.toml`, regenerates `uv.lock`, updates `CHANGELOG.md`,
   and creates a tag such as `v0.1.1`. The lockfile hook runs before the release
   commit and tag are created.
5. Verify and build the package:

   ```bash
   git show --stat --oneline HEAD
   git status --short
   git tag --points-at HEAD
   uv run hiraku --version
   uv lock --check
   uv build
   ```

6. Push the release commit and tag:

   ```bash
   git push origin main --follow-tags
   ```

When PyPI publication is enabled, publish the verified artifacts with
`uv publish`.
