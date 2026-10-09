# Hiraku tree navigation and tracking links: implementation tickets

Status: ready for implementation; no tickets implemented by this document.
Prepared: 2026-10-09. Baseline branch: `fix/dashboard-review`.
Checkpoint: `39f9253` (design context), following `63ceac5` (current demo code).

This is the next increment after `hiraku-dashboard-implementation-plan.md`.
For this increment, the decisions and ticket contracts below take precedence
over that older plan. This document provides enough context to start a ticket
without a repository survey. Read the named implementation files when making
changes, and follow any newer repository instructions or upstream changes.

## Outcome and scope

Hiraku is a Python library and CLI for research records, entrypoint launches,
and tracked runs. Records form a parent/child hierarchy and inherit configuration.
A record can own many tracked runs, and some records have none. The GUI should
explain that hierarchy and let researchers move from an experiment to its runs
and then to the tracking backend's detailed history and artifacts.

Implement an expandable, stable hierarchical graph of **records**, linked to a
record detail panel and a run detail panel. Runs remain in a table, not graph
nodes. Selecting a record shows its own runs by default; users can choose its
subtree or all attributed runs in its selected tracking source. Keep Progress
and Pareto comparisons. Provide a direct external action on a selected run.

No record editing, launching from the GUI, reparenting, backend embedding,
console-log ingestion, force simulation, or replacement React application in
this increment. Do not assume backend run pages contain console logs: label
actions “Open in MLflow”, “Open in W&B”, or “Open in tracking backend”.

## Existing implementation and constraints

The app is a packaged **marimo 0.25.x reactive Python notebook**, not React.
Dashboard extras: `marimo>=0.25.1,<0.26`, `altair>=6.3.0`, `pandas>=2.3.3`.
MLflow is pinned to `3.16.0`. Keep dashboard dependencies optional and avoid
eager imports of optional integrations in the core package.

| File | Current responsibility / extension point |
| --- | --- |
| `src/hiraku/dashboard/app.py` | `marimo.App(width="wide")`; `@app.cell` functions; `mo.state` supplies `get_record`/`set_record`; searchable dropdown selects records; radio selects record/source scope; run table uses single selection; selected-run panel has “Open record”; Altair plots support point selection. |
| `src/hiraku/dashboard/data.py` | Plain dataclasses, read-only record index, inheritance resolution, backend construction, snapshot building, source identity and run normalization. |
| `src/hiraku/dashboard/session.py` | `DashboardSession.reload_records()` starts refresh generation and clears source cache; `refresh(record_id, scope, use_active_record=True, cached=False)` coalesces concurrent requests and preserves matching last good snapshots on failures; `selected_run` stores composite run key. |
| `src/hiraku/dashboard/views.py` | Pure pandas helpers: record/run tables, metric/parameter choices, sorting, research-objective plot defaults. |
| `src/hiraku/dashboard/plots.py` | Pure Progress/Pareto transformations and metric-direction resolution; missing/nonfinite metrics are omitted, never zero-filled. |
| `src/hiraku/dashboard/launcher.py` | Starts `python -m marimo run` on loopback, default port 8765; copies bundled notebook into temporary directory and uses temporary working directory; passes project/record/scope through environment. |
| `src/hiraku/cli.py` | `dashboard_command`, Click `hiraku dashboard --record ID --scope record\|source --port N --no-browser`; delegates to launcher. |
| `src/hiraku/run.py` | Frozen Pydantic `Run`: `id`, metrics, params, `record_id`, commit, timestamp, entrypoint, optional `url`, raw metadata. No new public URL field is needed. |
| `src/hiraku/tracking/mlflow.py` | `MLflowBackendConfig` forbids unknown fields; settings currently type/tracking_uri/experiment_name/database. Adapter emits `Run(url=None)` and raw experiment ID. Read-only SQLite queries copy the database to a temporary snapshot and construct another MLflowBackend reader. |
| `src/hiraku/tracking/registry.py` | `backend_for(specification, config)` validates configuration; `_mlflow_factory` explicitly forwards MLflow settings. |
| `src/hiraku/tracking/wandb.py` | Already copies native `run.url` into normalized Run. |
| `src/hiraku/tracking/csv.py` | Supports optional CSV `url` column. Missing file reads must remain nonmutating. |
| `src/hiraku/dashboard/theme.css`, `branding.py`, `assets/` | Existing SDSC CSS tokens, packaged logo data URIs and SVG assets. |
| `scripts/test_dashboard.py`, `scripts/README-dashboard.md` | Real Digits MLflow demo, optional CSV fixture, coordinated server startup/shutdown and browser checks. |
| `docs/dashboard.md`, `docs/guides/tracking-mlflow.md`, `docs/tracking.md` | User documentation to extend. Python API reference also exists under `docs/api/python/`. |

### Snapshot contracts

`RecordView` contains `id`, `parent_id`, `depth`, type/status/decision,
commit/branch, markdown, effective `fields`, defining-record `sources`, effective
metric declarations, sanitized tracking_backend, and `has_runs=False`.
The existing builder does not populate reliable global run counts: do not
treat `has_runs` as an authoritative project-wide summary.

`RunView` contains key/id/record_id/timestamp/commit/entrypoint/url,
metrics/params/source. Its key is `source_identity + "::" + run.id`.
Never identify a selected run by bare ID: different sources may reuse IDs.

`DashboardSnapshot` version is currently 1 and contains project_root,
active_record_id, selected_record_id, scope, records, runs, a singular optional
`SourceInfo`, errors, refreshed_at and stale. `SourceInfo` has
name/identity/label/record_id/scope. Backend display fields are allowlisted;
tracking URIs and credentials are excluded from record display.

`build_snapshot(config, record_id=None, scope="record", ...)` currently accepts
only record/source. It resolves inheritance from one in-memory read-only index.
Explicit invalid IDs raise RecordNotFoundError; absent selection can fall back
to persisted active record, controlled by `use_active_record`. It fetches all
attributed runs from the selected source then filters for record scope.
The session cache is keyed by source identity; sort/plot/UI operations use
cached runs rather than re-querying. `_list_source_runs` supports the existing
`list_all_recorded_runs` adapter API and a future `list_runs(record_id=None)`.

CSV identity includes resolved path; MLflow identity hashes tracking URI and
includes experiment name; W&B identity includes entity/project. Unknown custom
backends currently have a default identity; do not silently merge differently
configured custom sources when extending subtree support.

Read-only is a hard requirement: do not call the mutating record index/reparent
recovery path, persist GUI selections to active context, create missing CSVs,
or let MLflow migrate the inspected SQLite database. Malformed records,
duplicate IDs, missing parents, cycles, transient file changes, and backend
failures already produce structured DashboardErrors. Preserve these behaviors.

### Demo and test fixtures

`uv run --all-extras python scripts/test_dashboard.py` creates a retained
temporary project from the bundled `digits-mlflow` example. Root `digits` has
children `weak-l2` and `strong-l2`; training uses alpha 0.0001/1.0, seed 42,
64 hidden units, and 40 epochs. Real Hiraku launches attribute record, commit,
and `train` entrypoint. Backend is project-local `mlflow.db`, experiment
`hiraku-digits`. Dashboard starts on 8765 selecting weak-l2 in source scope;
MLflow UI starts on 5000. Both ports are configurable. No data download or
credentials are needed. Checks are opt-in with `--run-tests`/`--browser-tests`.

Default plots compare latest per-run `val/log_loss` (minimize) and
`val/accuracy` (maximize). Full epoch curves and `plots/learning-curves.png`,
`metrics/epoch-metrics.csv`, `results/final-metrics.json` live in MLflow.
`mlflow_training_url(base_url)` currently resolves experiment ID via the
server API and opens `/#/experiments/ID/runs?workflowType=machine_learning`.
This is an experiment link, not an individual run URL.

`tests/dashboard/fixtures.py:create_acceptance_project` creates baseline,
child-a, child-b, and unrelated records. Baseline and children use distinct
CSV files; both children reuse run ID `a-1`; child-a also has a missing-metric
run. Metrics acc/loss have maximize/minimize directions. This is the primary
multi-source subtree fixture. Existing tests cover nonmutation, stale refresh,
single-query caching, 1000 records/10000 runs, notebook export and browsers.

## Shared interaction and data decisions

1. Record graph: stable left-to-right hierarchy, sorted siblings, multiple roots,
   separate expand/collapse and select actions. Selection highlights ancestry.
   Search finds IDs and descriptions, reveals ancestors, selects and brings the
   result into view. Show visible focus and keyboard navigation.
2. Record selection is the single source of truth shared by graph, search,
   dropdown fallback, breadcrumb, and run “Open record”. Preserve expansion and
   selection across refresh where IDs still exist; removed IDs get explicit
   feedback, not an unrelated automatic selection.
3. Scopes: `record` = selected record only; new `subtree` = selected record plus
   descendants across their effective tracking sources; `source` retains the
   current selected-source meaning. Selecting a parent never silently changes
   scope. Default CLI scope remains record for compatibility.
4. Subtree queries resolve each member's effective backend, group by actual
   source/query identity, query each distinct source once per generation, and
   retain runs attributed to the members assigned to that source. This avoids
   leaking unrelated records or collecting obsolete attribution from another
   member's source. Deduplicate by composite key, never bare run ID.
5. Summaries use loaded scope/source data, with coverage clearly stated. Show
   “not loaded” for unknown counts rather than zero. Optional chosen metric
   summary = best finite own-record value among loaded runs, using effective
   declared direction; ambiguous direction gives no best-value summary. Do not
   query every backend just to decorate the graph.
6. Backend URL is adapter-produced `Run.url`, copied through RunView. Only
   HTTP(S) links are clickable; render escaped labels/attributes and use
   `target="_blank" rel="noopener noreferrer"`. Missing/invalid URL yields
   explanatory text. Credentials must not appear in links or snapshots.

## Ticket overview and dependencies

| Ticket | Deliverable | Depends on |
| --- | --- | --- |
| GUI-01 | Pure hierarchy projection and navigation state | Baseline |
| GUI-02 | Multi-source subtree snapshots and scope plumbing | Baseline |
| GUI-03 | Adapter-owned MLflow run URLs | Baseline |
| GUI-04 | Interactive record graph component | GUI-01 |
| GUI-05 | Integrated workspace, scopes and run links | GUI-02, GUI-03, GUI-04 |
| GUI-06 | Demo wiring, end-to-end validation and documentation | GUI-05 |

GUI-01/02/03 can be assigned independently. GUI-04 must use GUI-01's exported
contract. Serialize edits to app.py between GUI-04/05, and to demo/docs between
GUI-03/06. Each agent implements its own tests; GUI-06 does integration checks.
Ticket IDs here are local plan identifiers, not created GitHub issues.

## GUI-01 — Hierarchy projection and navigation state

Files: add `src/hiraku/dashboard/tree.py` and
`tests/dashboard/test_dashboard_tree.py`; leave backend queries outside tree.py.

Implement typed pure helpers for adjacency, ancestry, descendant membership,
search, deterministic visible layout and navigation-state reconciliation.
Input is RecordView sequence plus selected ID, expanded ID set, and search
query. Return nodes keyed by record ID, edges, ancestry, visible order and
coordinates; expose stable contracts for GUI-04. Document the precise helper
signatures in the module and ticket handoff. Prefer iterative traversal for
deep trees. Invalid relationships must terminate and keep records reachable
in a clearly marked disconnected group; do not invent valid parentage.

Acceptance: unit tests for empty forest, multiple roots, sorted siblings,
collapsed branches, search reveal, removed selections, deep chain, missing
parent and cycle. 1000 records must be handled without recursion failure.
Search/layout/state operations must neither touch disk nor query backends.

## GUI-02 — Subtree snapshots across tracking sources

Files: data.py, session.py, launcher.py, cli.py, affected snapshot consumers;
tests/dashboard/test_query_data.py, test_dashboard_session.py,
test_dashboard_launcher.py, test_dashboard_acceptance.py and relevant CLI tests.

Add subtree scope end to end. Reuse the read-only index and effective values.
Introduce additive `sources: tuple[SourceInfo, ...]` snapshot metadata with a
safe default for existing construction sites; retain singular `source` as the
selected record's source for compatibility. Bump SNAPSHOT_VERSION to 2 and
update assertions/docs accordingly. Publish source coverage/failure metadata
so UI can distinguish partial results from complete zero-run results.

Keep record/source behavior intact. Separate storage identity from query/cache
identity if needed; different UI base URLs for the same store must not return
another configuration's cached links. Custom source configurations require
distinct cache identities; hash sensitive values rather than displaying them.
For a partially failed subtree, show successful-source results with explicit
partial status; do not replace them wholesale with unrelated stale data.
Matching previously complete snapshots can be offered as stale only if the
UI clearly distinguishes their generation/coverage. Define and test this
policy in DashboardSession. Sort and graph navigation reuse the cache.

Acceptance: baseline subtree includes base-1 and both distinct a-1 runs plus
a-missing; excludes u-1. Shared inherited sources are queried once per refresh;
different sources remain separate; unrelated attribution is excluded. Test
leaf/empty subtree, invalid scope, cycle termination, backend override,
one failing source, refresh invalidation and unchanged project bytes/context.
CLI `--scope subtree` reaches launcher; existing options remain valid.

## GUI-03 — MLflow run URLs owned by the backend adapter

Files: tracking/mlflow.py, tracking/registry.py;
tests/tracking/test_mlflow.py and test_registry.py; MLflow guide/API docs.

Add optional `ui_url` to MLflowBackendConfig and constructor, forward in factory
and in the read-only snapshot reader. Explicit UI URL takes precedence. If
absent, an HTTP(S) tracking_uri may serve as UI base only when it is a valid
credential-free web URL; SQLite/file/Databricks sources without a web UI base
retain None. Validate explicit settings as absolute HTTP(S) URLs with host,
no embedded credentials/query/fragment; allow port and deployment path prefix.
Never infer localhost:5000 from database storage. Preserve source conflict
validation and optional import behavior.

Construct run deep links from configured base, native experiment ID and run ID;
encode identifiers and preserve any deployment prefix. Verify the exact run
route against pinned MLflow 3.16.0 rather than copying the experiment route.
No network call is needed to build a URL, as native info has both IDs.

Acceptance: tests for explicit precedence, HTTP base fallback, no URL for
SQLite without ui_url, custom port/prefix/trailing slash, invalid schemes,
credential rejection, encoded IDs, registry forwarding and read-only cloning.
The original database remains unchanged. W&B and CSV URL behavior stays intact.
Include user-facing YAML example and document missing-UI behavior.

## GUI-04 — Interactive record graph

Files: new dashboard tree component module as necessary, theme.css, app.py
integration hook, test_dashboard_tree.py and browser component checks.

Implement GUI-01 layout in the existing marimo runtime, with edges and labeled
record nodes, separate expansion controls, selected/ancestor styles, search,
fit/recenter and pan/zoom when the graph exceeds its container. Use native
marimo controls or a locally bundled component with a documented Python
selection/expansion event bridge. Static SVG alone does not meet acceptance.
Avoid CDN scripts and network-loaded assets. If a dependency is necessary,
add it through uv and document why existing runtime facilities were insufficient.

Use SDSC tokens: dark #26245c, interactive #5461a6, accent #90ca42, light
#dddeec, surfaces/borders from theme.css. Keep existing Space Grotesk/Switzer
fallback stacks and packaged logos. UI_KIT_GUIDELINES.md is visual guidance;
its React/Tailwind examples do not mandate a framework migration.

Acceptance: selecting a node emits its ID; expanding does not select it;
search exposes collapsed matches; keyboard selection/expansion and focus work;
selected ancestry is visible; multiple roots/invalid relationships remain
reachable; no layout simulation jitters on rerender. At narrow widths offer a
usable tree/list fallback. Test a real browser event bridge, not only markup.

## GUI-05 — Compose the research workspace and backend actions

Files: app.py, views.py, session.py as needed, theme.css;
tests/dashboard/test_dashboard_views.py, test_dashboard_browser.py,
test_dashboard_regressions.py and notebook render acceptance.

Compose graph/navigation beside the selected record details; place run table,
run details and Progress/Pareto comparison in an adjacent workspace. Use
responsive stacking on small screens. Retain descriptions, effective values
and defining-record provenance without dumping every field above the runs.
Show breadcrumb, scope labels “This record”, “This subtree”, and
“All attributed runs in this source”, coverage/errors and refresh time.

Wire all navigation paths to shared selected-record state. Preserve composite
selected-run identity through sorting/refresh if still in scope; clear obsolete
run details when a record/scope change removes that run. Keep plot selections
consistent with current scope; conflicting metric directions require explicit
choice rather than a guessed optimization direction.

Primary external action belongs beside selected-run ID/provenance, with backend
label resolved from source metadata. Add a row shortcut if marimo supports it
without compromising row selection/accessibility; otherwise the primary action
fulfills this increment. Preserve run-to-record navigation. Validate/escape
links at rendering boundary even for CSV/custom backend values.

Acceptance: digits parent in subtree scope shows both children; record scope
shows no parent-owned runs. Selecting weak-l2 shows its own run. “Open record”
updates graph and breadcrumb. Run link opens the exact selected run in a new
tab; missing/unsafe links show a useful unavailable state. Sorting and graph
expansion cause no new backend queries. Scope/refresh changes never show a
different source's reused run ID. No project/context mutation; export renders.

## GUI-06 — Demo, documentation and integrated acceptance

Files: scripts/test_dashboard.py, scripts/README-dashboard.md,
tests/test_dashboard_demo.py, tests/dashboard/test_dashboard_browser.py,
tests/dashboard/test_dashboard_acceptance.py, docs/dashboard.md and changelog.

Provide the actual configured MLflow UI base from `--mlflow-port` to the demo
backend ui_url before dashboard reads. Prefer setting demo-only generated
backend configuration using public APIs, not a global environment override
that could leak to other projects. Training storage remains demo-local SQLite;
custom ports work; CSV mode never requires MLflow. Retain coordinated startup,
failure cleanup, Ctrl-C behavior, opt-in checks and existing-directory protection.
Consider starting the Digits demo at digits/subtree so the hierarchy is explained
immediately; update all initial-state tests and walkthrough text if doing so.

Extend automated browser flow for graph selection/expansion/search, parent
subtree comparison, run provenance navigation, exact link href/new-tab behavior,
missing URL and refresh. Assert generated MLflow URL refers to native run ID
and verify it opens that run on the pinned local UI. Add mult-source reused-ID
coverage and file-byte/context assertions. Document known limitations and how
to configure ui_url in normal projects. Keep original behavior tests.

Acceptance: fresh default and custom-port Digits demos expose two real runs and
working exact backend links; CSV edge-case demo works offline. Full checks below
pass; browser-dependent checks are run when driver is available and otherwise
reported explicitly as unrun. Do not describe skipped browser checks as passed.

## Execution rules, validation and handoff

Use Python >=3.11, uv for every project Python command, pathlib and subprocess
argument lists. Use apply_patch for source edits, typed small functions,
domain exceptions from errors.py, Ruff E/F/I/UP/B/SIM and 88-character lines.
Do not hand-edit uv.lock. Do not commit site/, dist/, caches, virtual environments,
.reasonix task state, credentials or machine-specific paths. Keep optional
integrations mocked/offline in regression tests. Follow architecture boundaries:
BaseRecord owns persistence, Record hierarchy, HirakuConfig discovery/context,
tracking adapters backend behavior, dashboard read-only presentation.

Setup:

```bash
uv sync --all-extras
uv run pre-commit install
```

Focused validation for each applicable ticket:

```bash
uv run pytest tests/dashboard
uv run pytest tests/tracking/test_mlflow.py tests/tracking/test_registry.py
uv run pytest tests/test_dashboard_demo.py
uv run ruff check src tests
```

Before completing code work, required full checks:

```bash
uv run pytest
uv run ruff check .
uv run zensical build --clean
```

If dependencies/package assets change, also `uv build`. Full pytest uses xdist;
browser tests are opt-in, so perform their explicit command separately:

```bash
uv run --all-extras python scripts/test_dashboard.py --browser-tests chrome --no-serve
uv run --all-extras python scripts/test_dashboard.py --no-browser
uv run --all-extras python scripts/test_dashboard.py --example csv --no-browser
```

Use a focused branch, Conventional Commit messages, no ordinary direct main
pushes or release tags. Before opening/updating a PR check related issues/PRs,
review complete diff, and include impact and actual validation. Human review
remains required. This plan authorizes implementation work, not publication.

Each ticket handoff must state: changed files, exported contracts, tests run and
results, remaining limitations, and dependent-ticket impacts. Coordinate changes
to shared files. Mark a ticket complete only when its acceptance criteria and
documentation are satisfied; record any unrun checks with their concrete reason.
