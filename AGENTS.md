# AGENTS instructions

These instructions apply to AI-assisted development of the
`SwissDataScienceCenter/hiraku` repository.

For contributor-facing guidance, see [CONTRIBUTING.md](CONTRIBUTING.md).

Hiraku is a Python library and CLI for documenting research records, launching
entrypoints, and querying tracked runs. Keep changes small, explicit, and
consistent with the existing public API.

## Environment Setup

- Use Python 3.11 or newer.
- Use `uv` for environments, commands, and dependency changes.
- Install the complete development environment with:

  ```bash
  uv sync --all-extras
  ```

- Run project Python commands through `uv run`; do not depend on packages
  installed in the host Python environment.
- Install local pre-commit hooks when working on several changes:

  ```bash
  uv run pre-commit install
  ```

- Do not edit `uv.lock` manually. Change dependencies through `uv` and commit
  the resulting lockfile update.

## Commands

Use focused commands while iterating:

```bash
uv run pytest tests/test_records.py
uv run pytest tests/test_cli.py::test_name
uv run ruff check src tests
```

Before finishing a code change, run the relevant full checks:

```bash
uv run pytest
uv run ruff check .
uv run zensical build --clean
```

For packaging changes, also run:

```bash
uv build
```

Useful project commands include:

```bash
uv run hiraku --help
uv run zensical serve
uv run python scripts/check_changelog.py
```

Releases are restricted to maintainers and administrators. From a clean,
up-to-date `main`, the release command runs checks, bumps the version, creates
the commit and tag, and pushes both:

```bash
uv run python scripts/release.py patch
```

Use `--dry-run` to run checks without changing Git state. Use `--skip-checks`
only when the checks have already been run separately.

## Repository Structure

The repository is a single `uv`-managed Python project.

| Path | Purpose |
| --- | --- |
| `src/hiraku/` | Installed Python package |
| `src/hiraku/base.py` | Generic Markdown record serialization and validation |
| `src/hiraku/markdown.py` | Markdown section parsing for record bodies |
| `src/hiraku/record.py` | Research records, entrypoints, and metrics |
| `src/hiraku/run.py` | Normalized tracked runs |
| `src/hiraku/config.py` | Project discovery, configuration, and active context |
| `src/hiraku/cli.py` | Click-based command-line interface |
| `src/hiraku/launch.py` | Entrypoint argument rendering and dispatch execution |
| `src/hiraku/tracking/` | Tracking backend adapters and run normalization |
| `src/hiraku/git.py` | Git branches, commits, and temporary worktrees |
| `src/hiraku/context.py` | Curated context records |
| `src/hiraku/workflow.py` | Workflows: `workflow.md` records whose `start` and replies run on sessions |
| `src/hiraku/session.py` | Workflow sessions: stored state and reply correlation |
| `src/hiraku/mcp.py` | Stdio MCP server (`MCPServer`): tools and prompts for records, runs, workflows, and sessions |
| `src/hiraku/resources/` | Bundled workflows, dispatchers, skills, and examples |
| `tests/` | Unit, CLI, integration, tracking, and example tests |
| `docs/` | Documentation sources |
| `scripts/` | Development and release utilities |
| `.github/workflows/` | CI, documentation, and release workflows |

Generated or transient paths:

- `site/` — generated documentation; do not edit or commit it.
- `dist/` — generated package artifacts; do not commit it.
- `__pycache__/`, `.pytest_cache/`, and virtual environments — do not commit.

### Architecture boundaries

- `BaseRecord` owns generic Markdown/YAML persistence and validation.
- `Record` owns research hierarchy, inheritance, and record-local fields.
- `HirakuConfig` owns project discovery and persistent active-context state.
- The CLI should call the public record/config/launch APIs rather than duplicate
  persistence or validation logic.
- Tracking backends are queried through their adapter interfaces. Keep backend-
  specific behavior inside `src/hiraku/tracking/`.
- Launches go through the configured dispatcher and must preserve attribution
  such as record, entrypoint, and commit metadata.
- Workflow semantics live in `workflow.py` and `session.py` and are transport
  independent: a `Workflow` starts and continues its `Session`s through
  `Workflow.run_start` and `Workflow.run_reply`. All MCP code, including the
  tools and prompts for records, sessions, and workflows, lives in `mcp.py`;
  no other module imports the MCP SDK.
- Bundled resources under `src/hiraku/resources/` are installed by
  `hiraku init`; changes to them may affect newly initialized projects and
  should include appropriate example or integration coverage.

## Coding Standards

- Follow the existing Ruff configuration: Python 3.11 syntax, an 88-character
  line limit, and the enabled `E`, `F`, `I`, `UP`, `B`, and `SIM` rules.
- Prefer small, typed functions with explicit validation and domain-specific
  exceptions from `src/hiraku/errors.py`.
- Preserve public imports and CLI behavior unless the change explicitly
  requires a breaking change.
- Keep Click command behavior explicit and test user-visible errors and output.
- Use `pathlib.Path` and argument-list subprocess calls in cross-platform
  development scripts. Avoid Unix-only shell constructs such as `awk`, `rm`,
  shell pipelines, and `&&`.
- Keep comments concise and explain why something is non-obvious; do not write
  comments that merely narrate the code.
- Do not add credentials, machine-specific paths, generated artifacts, or
  unrelated formatting changes.
- Use `apply_patch` for source edits when working as an agent.

Update documentation when changing public Python APIs, CLI behavior,
configuration or record formats, bundled resources, or files under `docs/`.

## Testing Standards

- Add or update regression tests for behavior changes and bug fixes.
- Do not weaken or delete existing tests to make a change pass.
- Prefer `pytest` tests over `unittest.TestCase`.
- Keep tests deterministic and independent of network access or external
  credentials. Mark tests that require optional integrations appropriately.
- Place unit tests in the corresponding `tests/test_*.py` module and tracking
  backend tests under `tests/tracking/`.
- Example workflows belong under `tests/examples/` and should exercise the
  user-facing example end to end.
- Use `pytest.mark.integration` for integration tests that require external
  services or optional dependencies.
- Test public CLI behavior with Click's `CliRunner` where possible.
- When changing serialization, configuration, launch attribution, or tracking
  behavior, include tests for both valid and invalid inputs.

## Commits and PRs

- Use a focused branch and keep the diff limited to the requested change.
- Prefer Conventional Commit prefixes such as `feat:`, `fix:`, `docs:`,
  `test:`, `refactor:`, and `chore:`; follow the repository's existing history
  when a change spans multiple categories.
- Before opening or updating a PR:
  1. Check for related issues and pull requests.
  2. Run the relevant focused and full validation commands.
  3. Review the complete diff and remove unrelated changes.
  4. Ensure tests and documentation match the changed behavior.
  5. Include the user-visible impact and validation performed in the PR body.
- Do not push directly to `main` for ordinary development. Direct release
  pushes are reserved for maintainers and administrators using
  `scripts/release.py`.
- Do not create a release tag manually outside the release command unless a
  maintainer explicitly requests it.
- The human contributor remains responsible for reviewing and approving
  AI-assisted changes.
