# Contributing to Hiraku

Issues and feature requests can be submitted through the [GitHub issue tracker](https://github.com/SwissDataScienceCenter/hiraku/issues).
For bugs, include the command, a minimal reproduction, Python version, and operating system.

User stories can be also submitted by creating an [issue with the label "user story"](https://github.com/SwissDataScienceCenter/hiraku/issues/new?labels=%22user%20story%22). A user story describes a feature or workflow from the perspective of a user. It should include a description of the problem, the intended workflow and user inteface, the desired outcome, and any relevant context.

## Development setup

Hiraku uses Python 3.11+.

```bash
git clone https://github.com/SwissDataScienceCenter/hiraku.git
cd hiraku
uv sync --all-extras dev
uv run hiraku --help
```

Install the pre-commit hooks after setting up the development environment:

```bash
uv run pre-commit install
```

The hooks run Ruff's lint fixer and formatter on every commit. Run them manually
across the repository with:

```bash
uv run pre-commit run --all-files
```

Install an optional tracking integration when working on it:

```bash
uv sync --extra mlflow
uv sync --extra wandb

# all extras
uv sync --all-extras
```

If you use the tool installation, make sure the reinstall after changes:

```bash
uv tool install ".[mlflow,wandb]" --force --reinstall
```

## Run checks

Run the focused checks before opening a pull request:

```bash
uv run pytest
uv run ruff check src tests
uv run zensical build --clean
```

Tests create temporary Hiraku projects and Git repositories. Live W&B and
MLflow tests should be optional or use fakes so the default test suite does not
require credentials or network access.

## Make changes

Keep the public model and CLI behavior explicit.
Pydantic models validate data at construction time; backend adapters should implement the shared tracking interface and preserve normalized `Run` fields. Add tests for new behavior and update the relevant guide or API doc when a public interface changes.

## Documentation and examples

Preview the documentation locally:

```bash
uv run zensical serve
```

Keep onboarding instructions short and put exact option lists in generated reference pages.
Example projects must have simple commands and should have an end-to-end test under `tests/examples/`.

## Commits and pull requests

Use a feature branch and keep commits focused.
[Conventional Commit](https://www.conventionalcommits.org/) prefixes such as `feat:`, `fix:`, `docs:`, `test:`, and `refactor:` are preferred.

A pull request should explain the user-visible change, list relevant tests, and call out configuration or compatibility changes.
