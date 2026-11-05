# AGENTS.md

Instructions for AI coding agents working on `SwissDataScienceCenter/hiraku`.

Hiraku is a Python library and CLI for documenting research records, launching commands, and tracking runs. Keep changes small, focused, and consistent with the existing public API.

See [CONTRIBUTING.md](CONTRIBUTING.md) for contributor workflows.

## Development

Use Python 3.12+ and `uv` for environments, dependencies, and commands.

```bash
uv sync --all-extras
```

Run focused tests during development:

```bash
uv run pytest tests/test_records.py
uv run pytest tests/test_cli.py::test_name
uv run ruff check src tests
```

Before finishing a code change, run:

```bash
uv run pytest
uv run ruff check .
uv run zensical build --clean --strict
```

Report any checks that fail or cannot be run. Do not claim validation that was not performed.

## Architecture

Hiraku is a single Python package under `src/hiraku/`.

Core responsibilities:

- **`Project`** (`project.py`) — Project discovery, settings, active-record state, and managed paths.
- **`Record` and `Vault`** (`record.py`) — Research records, hierarchy, Markdown/YAML persistence, and metric inheritance.
- **`Run`** (`tracking/base.py`) — Normalized representation of a tracked execution.
- **Execution** (`execution.py`) — Record resolution, command launching, and tracking attribution.
- **Tracking** (`tracking/`) — Backend adapters implementing `TrackingBackend`; the factory validates settings and creates adapters.
- **Workflows** (`workflows/`) — LangGraph workflow discovery, execution, session history, and checkpoints.
- **CLI and MCP** (`cli.py`, `mcp.py`) — User-facing interfaces to the underlying APIs.

Respect these boundaries:

- Keep persistence and validation in their owning modules, not in CLI or MCP handlers.
- Methods requiring project context accept `project: Project`. Use `config` only for settings data.
- `Vault.resolve()` returns effective record values and their sources without modifying persisted records; only metric declarations inherit.
- `Vault.save()` validates one record and its file without scanning the collection. Use vault-level validation for cross-record integrity.
- Tracking adapters return normalized `Run` objects.
- Tracking backend configuration and the active record are project-wide. Set the active record through `Project.set_active_record()` so its name and ID stay in sync.
- Records use filesystem-friendly names for Markdown files in the records directory; reject filename collisions and preserve discovery of existing nested records.
- Import workflow APIs from their defining modules. Do not add eager imports or re-exports to `workflows/__init__.py`.
- Keep MCP SDK imports in `mcp.py`.
- Keep CLI and MCP behavior consistent when they expose the same functionality.

`hiraku init` installs bundled collaboration resources and skills. Changes under `src/hiraku/resources/` can affect newly initialized projects and require appropriate test coverage.

See [docs/guides/workflows.md](docs/guides/workflows.md) for workflow contracts.

## Coding conventions

- Follow the existing Ruff configuration: 88-character lines and Python 3.12 syntax.
- Prefer small, typed functions and explicit validation.
- Use domain-specific exceptions from `errors.py` where appropriate.
- Preserve public imports, CLI behavior, and persisted formats unless explicitly changing them.
- Use `pathlib.Path` and subprocess argument lists for cross-platform compatibility.
- Avoid shell-dependent commands in Python scripts.
- Keep comments concise and explain non-obvious decisions rather than narrating code.
- Use `apply_patch` for source edits.
- Avoid unrelated refactoring, formatting changes, new dependencies, and generated artifacts.
- Never commit credentials, machine-specific paths, or files under `site/`.

Follow existing patterns before introducing new abstractions.

## Testing

Add or update regression tests for behavior changes and bug fixes.

- Use pytest functions, not `unittest.TestCase`.
- Keep tests deterministic and independent of network access or external credentials.
- Use Click's `CliRunner` to test CLI output, errors, and exit codes.
- Test valid and invalid inputs when changing serialization, configuration, execution, or tracking.
- Keep tracking adapter tests under `tests/tracking/`.
- Put end-to-end example coverage under `tests/examples/`.
- Mark tests requiring external services or optional integrations with `pytest.mark.integration`.
- Do not weaken or remove existing tests to make a change pass. Verify first if a test is still valid, then update it to reflect the new behavior.

Prefer focused tests while iterating, then run the full validation commands before finishing.

## Documentation

Update documentation when changing:

- Public Python APIs or CLI behavior.
- Configuration, record formats, or persistence.
- Bundled skills, examples, or initialization behavior.
- Workflow contracts or user-facing functionality.

Keep documentation consistent with the implementation. Do not modify generated documentation in `site/`.

## Git and releases

- Work on a focused branch; do not push ordinary changes directly to `main`.
- Check for related issues or pull requests before substantial changes.
- Review the complete diff and remove unrelated modifications.
- Prefer Conventional Commit prefixes (`feat:`, `fix:`, `docs:`, `test:`, `refactor:`, `chore:`).
- Include user-visible changes and validation results in pull request descriptions.
- Do not create release tags manually or invoke release commands unless explicitly requested by a maintainer.

Release procedures are maintained in `scripts/release.py`.

The human contributor remains responsible for reviewing and approving AI-assisted changes.
