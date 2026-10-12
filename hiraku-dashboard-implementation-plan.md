# Hiraku dashboard implementation plan

Implement [Hiraku issue 78](https://github.com/SwissDataScienceCenter/hiraku/issues/78) as an optional, local, view-only marimo application. Hiraku owns research records and tracking adapters; marimo supplies presentation and interaction. Use OpenResearch only to inform interaction design, without copying its application code, components, assets, or layout wholesale.

Implementation repository: `SwissDataScienceCenter/hiraku`. Ticket IDs below are local planning identifiers, not GitHub issues that have already been created. This document records the original implementation plan; see [the dashboard guide](docs/dashboard.md) for the delivered behavior.

The deliverable is `hiraku dashboard`: browse records, inspect their provenance, select a tracking source, sort runs, and switch between progress and Pareto plots. Exclude record editing, experiment launching, agent chat, central authentication, shared hosting, and within-run training curves. Allocate **7–11 engineer-days plus two contingency days**; protect the remaining monthly capacity for scientific provenance work.

**Agent execution contract.** Read the implementation repository's current `AGENTS.md` and `CONTRIBUTING.md`. Preserve existing changes, use a feature branch, and use `apply_patch` for edits. Run Python commands through `uv run`. Keep each ticket reviewable, record its validation results, and finish dependencies before dependent tickets. Do not publish issues, push branches, merge PRs, or release packages solely on the authority of this plan. Browser execution is local; bind to loopback. Installing dependencies and implementing the tickets belong to the subsequent implementation task, not this planning task.

| Ticket | Deliverable | Depends on | Estimate |
| --- | --- | --- | --- |
| HD-00 | Environment and tracking API baseline | None | 0.5 day |
| HD-01 | Marimo feasibility slice and optional dependencies | HD-00 | 0.5–1 day |
| HD-02 | Read-only dashboard query layer | HD-01 | 1.5–2 days |
| HD-03 | Packaged dashboard command and server lifecycle | HD-01 | 0.5–1 day |
| HD-04 | Record browser and run table | HD-02, HD-03 | 1–1.5 days |
| HD-05 | Progress and Pareto views | HD-04 | 1–1.5 days |
| HD-06 | Refresh and custom notebook support | HD-05 | 0.5–1 day |
| HD-07 | Acceptance tests, documentation and delivery | HD-02 through HD-06 | 1.5–2 days |

**HD-00 — Install the environment and establish the API baseline**

Inspect repository status before creating a new branch. If unrelated changes overlap the work, isolate them rather than stashing or overwriting them. Recheck [PR 77](https://github.com/SwissDataScienceCenter/hiraku/pull/77): the inspected checkout was `ba044b1`, and that PR was open during assessment. Its proposed contract is `record.resolve_tracking_backend(config=...)`, `backend.list_runs()` for the source, and `backend.list_runs(record_id=...)` for one record.

Prefer the merged contract when available. If the PR is still open, record the current contract and isolate compatibility in the dashboard query layer; do not merge or cherry-pick another contributor's branch automatically. UI work can continue against fixtures. Finish integration against the actual API before HD-07; do not duplicate backend resolution in notebook cells.

Use the existing `uv` installation when available. On the inspected Mac, `uv 0.11.2` was installed. If absent, use Homebrew on macOS or the official installer for Linux; these are alternatives, not consecutive steps. Follow environment approval requirements for installation/network access. [Installation instructions](https://docs.astral.sh/uv/getting-started/installation/)

```sh
# macOS, only when uv is absent
brew install uv
```

```sh
# Linux alternative, only when uv is absent
curl -LsSf https://astral.sh/uv/install.sh | sh
```

From the Hiraku checkout, after choosing a clean feature branch:

```sh
uv --version
uv python install 3.12
uv sync --locked --all-extras --python 3.12
uv run --locked --all-extras python --version
uv run --locked --all-extras hiraku --help
uv run --locked --all-extras pre-commit install
uv run --locked --all-extras pytest
uv run --locked --all-extras ruff check .
```

`uv sync` creates the project `.venv`; shell activation is unnecessary. Preserve the existing lockfile during baseline setup. If it is inconsistent, diagnose that separately before changing dependency versions. Do not commit `.venv`, caches, or a machine-specific Python path. Python 3.12 is the development default for this plan; retain the repository's supported Python range. [Locking and syncing](https://docs.astral.sh/uv/concepts/projects/sync/)

Acceptance: record the base commit, Python/uv versions, PR 77 status, baseline failures/skips, and selected API contract. No unexplained baseline failures are attributed to the dashboard.

**HD-01 — Prove the marimo interaction and add optional dependencies**

Add dashboard dependencies through uv, keeping the core installation lightweight:

```sh
uv add --optional dashboard marimo altair pandas
uv sync --locked --all-extras
uv run --locked --all-extras marimo --version
uv run --locked --all-extras marimo run --help
```

Commit `pyproject.toml` and the uv-generated `uv.lock` together. Check that the resolved packages support Hiraku's Python matrix; do not silently raise the core Python minimum. Set dependency compatibility bounds based on the versions exercised by the prototype, avoiding an unrelated dependency upgrade.

Build a fixture-backed notebook with record selection, a run table, and a selectable progress chart. Selecting a record or point must update a detail panel. Use public marimo APIs and Altair. Verify the exact installed CLI flags before designing the production launcher. Marimo's application mode serves a notebook with its code hidden; interactive plotting can return selected data to Python. [Application mode](https://docs.marimo.io/guides/apps/), [plotting](https://docs.marimo.io/guides/working_with_data/plotting/)

Acceptance: demonstrate this interaction in a local browser within one day, including a record without runs. The CLI remains importable without marimo. If the basic interaction requires substantial custom JavaScript or marimo internals, document the failed criterion and reassess before expanding implementation.

**HD-02 — Implement a non-mutating query layer**

Suggested files: `src/hiraku/dashboard/data.py`, supporting tests under `tests/dashboard/`, and a narrow read-only extension to the existing record loader where necessary. Keep domain operations in Hiraku; the notebook must not parse YAML or call tracking services directly.

Return a versioned presentation snapshot containing records, resolved field origins, normalized run summaries, selected source identity, scope, refresh time, and structured errors. Use plain Python structures in the query layer; convert to plotting dataframes at the presentation boundary. Identify runs by `(source identity, run ID)` to avoid collisions. Expose an allowlist of display metadata rather than serializing backend configuration or credentials.

Implement these semantics:

- Load the record index once per refresh and resolve inheritance against that index. Avoid rereading every record for every table row.
- Resolve the tracking source from the selected record through the agreed Hiraku API. Default to that record's runs; offer a clearly labelled “all attributed runs in this source” scope. That scope is not a descendant-tree query.
- With no selected or active record, show the record browser and request a selection. Browsing must not set the persistent CLI active record.
- Query a selected source once per refresh. Selection, sorting and chart changes operate on the cached snapshot. Keep session-specific selection separate from shared configuration.
- Detect incomplete or malformed metadata without repairing it. Existing `list_records()` calls `_record_index()`, which invokes `_recover_reparenting()` and can unlink files. Add an explicit non-mutating read path while preserving current recovery behaviour for existing callers.
- On concurrent file changes, allow one bounded reread, then show a clear error or the labelled last successful snapshot. Avoid unbounded retry loops. Missing local tracking storage must not be created merely by viewing the dashboard.

Acceptance tests: inherited backend configuration; record/source scope distinction; two sources with identical run IDs; missing records/metrics; malformed records; pending reparent journals in both phases; concurrent replacement; unavailable backend. Compare research files and active configuration before/after queries and verify that recovery functions do not execute. Include an adapter call-count assertion. Keep tests credential-free using fake adapters and temporary projects.

**HD-03 — Add the command and package the application**

Suggested files: `src/hiraku/dashboard/launcher.py`, `src/hiraku/dashboard/app.py`, and a small addition to `src/hiraku/cli.py`. Ship the default notebook in the Python package, rather than copying it into every research project through `hiraku init`.

Implement the following proposed interface; it does not exist yet:

```sh
hiraku dashboard
hiraku dashboard --record RECORD_ID
hiraku dashboard --port 8765 --no-browser
```

Resolve the research project before serving. Launch marimo in application mode using the current environment's Python, argument-list subprocess calls, and the verified CLI contract. Preserve project context even when the packaged notebook resides elsewhere. Manage resource lifetime correctly when locating the bundled notebook. A separate FastAPI service is unnecessary for this release.

Bind to `127.0.0.1`; do not provide shared-host deployment in this ticket. Keep marimo session/cache files outside research records. Handle occupied ports, invalid record IDs, missing project configuration, missing dashboard extras, and Ctrl-C. Stop the owned child process when the CLI exits. Print an actionable install command when dependencies are absent; never install packages automatically from this command.

Acceptance: CLI tests cover argument forwarding, error messages and shutdown. Core `hiraku --help` works without dashboard packages. A built-wheel smoke test launches the packaged notebook from an unrelated temporary project directory. No production data is used for these checks.

**HD-04 — Build the record browser and run table**

Use a searchable record list with parent/depth columns as the first hierarchy representation; issue 78 permits a tree or list. Pair it with a detail panel and sortable run table. Display record type/status where supplied, Markdown description, explicit versus inherited fields, defining record IDs, and the recorded commit. Preserve the user's selection by ID when rows are sorted or refreshed.

Display run ID, record link, timestamp, commit, selected metrics and parameters. Label the selected tracking source and record/source scope. Distinguish “no runs”, “backend unavailable”, and “metadata unavailable”. Missing values remain missing; they must not become zero. Do not infer successful execution from a present metric. Render descriptions as content, never as executable notebook code.

Acceptance: selecting a record updates details and the intended run scope; selecting a run reveals its provenance. Browser checks cover empty projects, long descriptions, missing metadata, repeated run IDs across sources, and numeric metric sorting. Reading and selecting change no research files or active-record setting. An interactive graph/tree and a code-diff viewer are follow-ups; show the commit hash now.

**HD-05 — Add progress and Pareto plots**

Suggested file: `src/hiraku/dashboard/plots.py`, with pure transformation functions tested independently of marimo.

Progress means one summary metric per run, plotted against run timestamp or a clearly labelled deterministic run index. Do not manufacture epoch histories from summary values. Use stable ordering and explicit handling for missing timestamps. Tooltips and point selection expose run ID, record ID, commit and exact metric value. Add a best-so-far overlay only when the metric direction is known.

Provide metric selectors for a two-objective scatter plot and Pareto frontier. Respect the record's metric directions where unambiguous; otherwise request explicit directions. Preserve all nondominated ties. Exclude missing/non-finite values from calculations with a visible omitted-row count. Compute the frontier from the selected comparison cohort, with the filtering scope displayed. Where evaluation-protocol metadata is available, allow grouping/filtering by it; never label heterogeneous results scientifically comparable merely because metric names match.

Acceptance: tests cover maximise/minimise combinations, ties, dominated points, negative values, missing values, NaN/infinity, empty results and deterministic ordering. Browser checks confirm plot selection reveals the correct record/run. Users can switch progress/Pareto views and axes without editing source or re-querying the tracker.

**HD-06 — Add refresh and controlled customisation**

Provide explicit refresh and opt-in periodic refresh, off by default. Show the last successful fetch time, an in-flight indicator, and stale/error status. Coalesce refresh requests so slow backends do not accumulate concurrent calls. Preserve selection where possible; report when a selected record disappears. Marimo provides a refresh control that reruns dependent cells. [Refresh API](https://docs.marimo.io/api/inputs/refresh/)

Add an optional `hiraku dashboard --notebook PATH` for an explicitly selected user-owned notebook. Document the snapshot interface and provide a short custom-plot example using it. Default startup always uses the bundled app: do not automatically discover and execute notebooks from a research checkout. A custom Python notebook is trusted executable code; the view-only guarantee applies to the shipped dashboard, not arbitrary user code. Run the selected notebook in application mode; editing is a separate, deliberate marimo workflow.

Acceptance: tests prove no overlapping refreshes, no tracker queries from purely visual changes, and correct stale/error handling. A custom notebook consumes the same snapshot interface without backend-specific code. The shipped notebook is never overwritten. Default view switching satisfies ordinary customisation without requiring a custom notebook.

**HD-07 — Verify, document and hand off**

Add a deterministic fixture with a baseline, two child investigations, an unrelated record, mixed metric directions, a missing-metric run, and multiple tracking-source identities. Add real local CSV coverage and a local MLflow smoke test using temporary storage. Mock W&B network access in normal tests; do not require external credentials.

Exercise the full browser path: start command, select record, change scope, sort, inspect provenance, select chart point, switch to Pareto, refresh, stop server. Include a synthetic 1,000-record/10,000-run case: measure fetch/render time separately, ensure queries are not issued per row, and confirm sorting/selection remains usable. Record hardware and timings rather than claiming a universal latency guarantee. Check pagination/truncation in adapters; the dashboard must not silently imply it has all source runs when results are capped.

Document installation, supported Python versions, commands, scopes, plot semantics, custom notebook trust, and troubleshooting. Update the changelog and documentation navigation. Test both core-only wheel installation and dashboard-extra installation in separate uv environments; include the default notebook in distribution assertions. Any added browser test tooling belongs in development dependencies, with its setup documented.

Run these checks from the implementation repository, preserving extras during uv execution:

```sh
uv sync --locked --all-extras
uv run --locked --all-extras pytest
uv run --locked --all-extras ruff check .
uv run --locked --all-extras ruff format --check .
uv run --locked --all-extras python scripts/check_changelog.py
uv run --locked --all-extras zensical build --clean
uv build
git diff --check
```

Run relevant focused tests during each preceding ticket. Follow the current CI matrix, including supported Python versions; report unavailable platform checks or existing failures precisely. Do not weaken tests to obtain a passing result. Full UI interaction must be exercised in a browser in addition to unit tests.

Acceptance: all implemented issue-78 requirements map to passing tests or recorded browser checks; source and active configuration remain unchanged after the shipped dashboard workflow. The handoff lists versions, changed files, test results, known limits and remaining follow-ups. Provide a reviewable local diff; publication remains a separate action.

**Delivery checkpoints.** At the end of day 2, HD-00/01 should establish the API contract and demonstrate marimo feasibility. Around day 5, aim for HD-02/03/04: an installable browser over real Hiraku data. Finish plots and refresh next, reserving the last two implementation days for HD-07. Reduce custom notebook support or advanced visual polish first if the schedule slips; retain read-only guarantees, correct scope, provenance links and test coverage. Such reductions must be stated as deferred acceptance criteria, not reported as a complete implementation of issue 78.

**Deferred tickets.** Create follow-up plans only when needed: metric-history APIs for epoch curves; interactive experiment graphs; code-diff inspection; broader cross-source aggregation; authenticated shared hosting. None is a dependency for the local MVP above.
