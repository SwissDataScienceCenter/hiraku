# AGENTS.md

Instructions for AI coding agents working on `SwissDataScienceCenter/hiraku`.

Hiraku is a Python library and CLI for documenting research records, launching commands, managing workflows, and tracking research runs.

**Primary goal:** Make the smallest correct change while preserving existing behavior, public APIs, data formats, and architectural boundaries.

See [CONTRIBUTING.md](CONTRIBUTING.md) for contributor workflows.

## 1. Working principles

Before making changes:

1. Understand the request and inspect the relevant implementation, tests, and documentation.
2. Follow existing patterns before introducing new abstractions.
3. Identify affected public interfaces, persistence formats, and dependent modules.
4. Check related issues or pull requests before substantial changes.
5. Keep the implementation and tests focused on the requested behavior.

When modifying code:

- Prefer simple, explicit solutions over unnecessary abstraction.
- Fix the underlying issue rather than adding workarounds.
- Preserve backward compatibility unless a breaking change is explicitly requested.
- Do not refactor unrelated code or introduce unrelated formatting changes.
- Do not overwrite or discard existing user modifications.
- Do not add dependencies unless necessary.
- Avoid speculative features and unnecessary complexity.
- Keep the change easy to review.

For complex changes, briefly identify the affected components and validation approach before editing.

## 2. Development environment

Use Python 3.11+ and `uv` for dependency management and commands.

Install the development environment:

```bash
uv sync --all-extras
```

Run Python commands through `uv run` rather than relying on globally installed packages.

Do not manually edit `uv.lock`. Update dependencies through `uv` and include the resulting lockfile changes when necessary.

### Validation during development

Run focused checks while iterating:

```bash
uv run pytest tests/test_records.py
uv run pytest tests/test_cli.py::test_name
uv run ruff check src tests
```

Choose the most relevant test module or individual test for the code being modified.

### Validation before completion

For code changes, run:

```bash
uv run pytest
uv run ruff check .
uv run zensical build --clean --strict
```

For packaging changes, also run:

```bash
uv build
```

If a command fails or cannot be executed, report that explicitly.

**Never claim that tests, linting, or builds passed unless they were actually executed successfully.**

## 3. Architecture

The public Python API lives under `src/hiraku/`.

Custom PSI/J executors live under `src/psij-descriptors/`.

### Component responsibilities

| Component | Location | Responsibility |
|---|---|---|
| Project | `project.py` | Project discovery, configuration, and managed paths |
| Record / Vault | `record.py` | Research document models and filesystem operations |
| Run | `tracking/base.py` | Normalized tracking traces, metrics, and attribution |
| Execution | `job.py` | PSI/J job preparation, execution integration, and submission history |
| Tracking | `tracking/` | Tracking backends and leaderboard configuration |
| Workflows | `workflows/` | LangGraph workflows, execution, sessions, and checkpoints |
| CLI | `cli.py` | Command-line interface |
| MCP | `mcp.py` | Model Context Protocol interface |

### Architectural boundaries

Respect the responsibilities of the owning modules.

**Project and configuration**

- Methods requiring project context accept `project: Project`.
- Use `config` only for settings data.
- Keep project discovery and managed path logic in `Project`.

**Records and persistence**

- `Record` owns document data; `Vault` owns filesystem access.
- `Record.id` is its OKF bundle-relative path without `.md`.
- Never persist `Record.id` in front matter.
- Record fields are local. Traverse declared relationships explicitly when ancestor information is needed.
- `Vault.save()` validates one record and its destination without scanning the collection.
- Replacing an existing file requires `overwrite=True`.
- Ordinary I/O must tolerate incomplete or cyclic graphs.
- Use explicit vault validation for cross-record integrity.
- Preserve discovery of existing nested records, enforce filesystem-friendly names, and reject filename collisions.

**Execution and tracking**

- Use PSI/J executors for job execution and status.
- Keep submission history keyed by PSI/J job ID.
- Preserve observed final outcomes in history.
- Tracking adapters return normalized `Run` objects.
- Keep backend-specific behavior inside `tracking/`.
- Leaderboards live under `hiraku/leaderboards/<name>.yml`.
- Records do not configure tracking metrics or sources.
- Record-scoped operations receive explicit record IDs.

**Workflows**

- Keep workflow discovery, execution, session history, and checkpoints in their owning modules.
- Import workflow APIs from their defining modules.
- Do not introduce eager imports or re-exports in `workflows/__init__.py`.
- Preserve established workflow contracts.

See [docs/guides/workflows.md](docs/guides/workflows.md).

**CLI and MCP**

- CLI and MCP handlers should delegate validation and persistence to the domain APIs.
- Do not duplicate business logic in interface layers.
- Keep equivalent CLI and MCP operations behaviorally consistent.
- Keep MCP SDK imports in `mcp.py`.

**Bundled resources**

`hiraku init` installs collaboration resources and skills from `src/hiraku/resources/`.

Changes to these resources may affect newly initialized projects. Include appropriate tests and documentation updates.

## 4. Coding conventions

Follow the existing Ruff configuration:

- Python 3.11 syntax.
- Maximum line length of 88 characters.
- Existing import ordering and lint rules.

Prefer:

- Small, typed functions with clear responsibilities.
- Explicit inputs, outputs, and validation.
- Domain-specific exceptions from `errors.py` where appropriate.
- `pathlib.Path` for filesystem operations.
- Subprocess argument lists rather than shell-dependent commands.
- Clear naming that expresses intent.
- Comments explaining non-obvious decisions.

Avoid:

- Unnecessary abstractions or generic frameworks.
- Broad exception handling that hides failures.
- Duplicated business logic.
- Unrelated formatting or refactoring.
- Unnecessary imports and hidden side effects.
- Comments that simply repeat what the code does.

Use `apply_patch` for source edits when available.

Preserve public imports, CLI behavior, and persisted formats unless explicitly changing their contracts.

## 5. Testing guidelines

Use `pytest` and maintain deterministic, isolated tests.

### Test organization

Organize tests by **feature or responsibility**, not by implementation chronology.

- Keep related tests together in the appropriate module.
- Keep tracking adapter tests under `tests/tracking/`.
- Keep end-to-end example tests under `tests/examples/`.
- Use descriptive names such as `test_record_create_rejects_invalid_id`.
- Avoid large test functions that verify unrelated behaviors.
- Preserve intentional end-to-end tests that validate complete user workflows.

Follow existing repository structure before creating new directories or modules.

### Fixtures and test utilities

- Use pytest fixtures for reusable setup and teardown.
- Place fixtures shared across modules in an appropriate `conftest.py`.
- Keep feature-specific fixtures close to the tests that use them.
- Request fixtures by name; do not import them directly from `conftest.py`.
- Prefer `tmp_path` and `monkeypatch` for filesystem and environment isolation.
- Use ordinary helper functions for reusable logic that does not require fixture lifecycle management.
- Avoid duplicating complex setup across tests.

### Test design

Each test should verify a clearly defined behavior.

Use the Arrange–Act–Assert pattern:

1. **Arrange:** Create only the required state and inputs.
2. **Act:** Execute the behavior being tested.
3. **Assert:** Verify the expected result and relevant side effects.

Guidelines:

- Test observable behavior rather than private implementation details.
- Test valid and invalid inputs when changing serialization, configuration, execution, or tracking.
- Include meaningful assertions for exit codes, output, errors, and persistence where appropriate.
- Use `pytest.mark.parametrize` for variations of the same behavior.
- Prefer explicit parameter case IDs when they improve failure reporting.
- Avoid redundant tests that cover the same behavior without providing additional confidence.
- Keep tests independent of execution order and shared mutable state.
- Avoid mocks when inexpensive, deterministic tests can exercise the real behavior.

### CLI testing

Use Click's `CliRunner` to test user-facing commands.

Verify relevant aspects of:

- Exit codes.
- Command output.
- Validation errors.
- Filesystem changes.
- State changes and persistence.

Use temporary project directories to avoid modifying real user data.

### Integration testing

- Keep ordinary tests independent of network access and external credentials.
- Mark tests requiring external services or optional integrations with `pytest.mark.integration`.
- Do not require external infrastructure for the default test suite.
- Keep integration scenarios focused on realistic user workflows.

### Regression coverage

For bug fixes:

1. Reproduce the incorrect behavior with a test.
2. Implement the smallest appropriate fix.
3. Verify that the regression test passes.
4. Run related existing tests.

For behavior changes, update existing tests and add new tests where the current coverage is insufficient.

**Never weaken or remove a valid test solely to make the suite pass.** First determine whether its expected behavior remains correct.

Do not reorganize the entire test suite unless test restructuring is part of the requested task.

## 6. Documentation

Update documentation when modifying:

- Public Python APIs or CLI behavior.
- Configuration or persistence formats.
- Record structure and validation rules.
- Bundled resources, examples, or initialization.
- Workflow contracts.
- User-facing features.

Keep documentation consistent with actual behavior.

Prefer updating existing documentation over creating redundant pages.

Do not manually modify generated files under `site/`.

## 7. Git and pull requests

Keep changes focused and reviewable.

- Work on a focused branch.
- Do not commit unrelated modifications.
- Never discard changes belonging to other contributors.
- Review the complete diff before completion.
- Do not commit credentials, secrets, machine-specific paths, or generated artifacts.
- Do not push directly to `main` during ordinary development.

Prefer Conventional Commit prefixes:

- `feat:` — new functionality
- `fix:` — bug fixes
- `test:` — test coverage
- `refactor:` — internal restructuring without behavior changes
- `docs:` — documentation
- `chore:` — maintenance

Pull request descriptions should include the user-visible change, relevant compatibility considerations, and validation performed.

Do not create release tags or invoke release commands unless explicitly requested by a maintainer.

Release procedures are maintained in `scripts/release.py`.

## 8. Completion checklist

Before reporting a task as complete:

- [ ] The implementation addresses the requested behavior.
- [ ] The change respects architectural boundaries.
- [ ] Existing public behavior and formats are preserved unless intentionally modified.
- [ ] Relevant tests were added or updated where needed.
- [ ] Focused tests pass.
- [ ] Full validation commands were executed, or limitations were recorded.
- [ ] Documentation is updated where required.
- [ ] The diff contains no unrelated changes or generated artifacts.

### Final response format

Summarize:

1. **Changes:** What was modified and why.
2. **Validation:** Commands executed and their results.
3. **Limitations:** Failed checks, unverified behavior, or remaining issues.

Do not claim unperformed validation or conceal known failures.

The human contributor remains responsible for reviewing and approving AI-assisted changes.