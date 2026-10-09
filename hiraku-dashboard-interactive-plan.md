# Interactive Hiraku dashboard: implementation tickets

Status: ready for implementation; this document does not implement the feature.
Prepared: 2026-10-09. Baseline: `8b3e825` on `fix/dashboard-review`.

## Agent assignment

Implement the tickets below in dependency order. Follow `AGENTS.md`, use Python
3.11+ and `uv`, and use `apply_patch` for source edits. Keep commits focused.
Read each ticket's named files before editing. Preserve existing untracked
files and unrelated changes. Do not push, create release tags, or publish issues
as part of this assignment. The ticket IDs here are local planning IDs.

This plan extends `hiraku-dashboard-implementation-plan.md` and
`hiraku-dashboard-tree-plan.md`. Their prohibition on GUI writes and launches
is superseded only for the explicit interactive mode described here. All
existing read-only behavior remains required in default mode.

## Intended workflow and decisions

1. Start `hiraku dashboard --interactive` in an initialized project.
2. Create a research root or a child experiment using a form.
3. Select the new record, inspect inherited configuration, and choose an
   effective entrypoint.
4. Override its arguments, review the command and dispatcher, then click
   **Launch run** once.
5. Continue browsing while the script runs; inspect launch status and output.
6. Refresh results after completion and inspect the backend-owned run.

Interpret “log a run” as executing an entrypoint whose script logs metrics,
matching the minimal, Heart Disease, Digits MLflow, and Iris W&B examples.
The GUI must state: “Your entrypoint logs results to the tracking backend.”
Do not add a generic metric-entry form or invent a backend write interface.
Manual logging is a separate future feature, requiring its own CSV/MLflow/W&B
write contracts. This increment fully supports creating records and launching
logged evaluations from the dashboard.

Keep the existing marimo app, record graph, detail panels, run table, Progress,
and Pareto views. Use existing SDSC styles and accessible marimo controls.
Do not replace the frontend or introduce a REST server, agent chat, record
editing/deletion/reparenting, Git branch management, or a scheduling system.

## Repository facts and APIs to preserve

| File | Verified implementation / extension point |
| --- | --- |
| `src/hiraku/dashboard/app.py` | marimo 0.25.x reactive notebook; `mo.state` owns selected record and expansion; graph widget survives navigation; refresh reloads record IDs, then fetches a cached snapshot. |
| `src/hiraku/dashboard/session.py` | `DashboardSession.reload_records()` clears source cache; `refresh()` coalesces queries and retains matching last-good snapshots; selected runs use source-qualified keys. |
| `src/hiraku/dashboard/data.py` | `read_record_index()` reads without reparent recovery; pending journal is reported; snapshots query each distinct source and preserve historical runs in source scope. Keep this module read-only. |
| `src/hiraku/record.py` | `Record.create(id, parent_id=..., related_to=..., markdown=..., activate=False, config=..., **fields)` validates and persists; `load_record()`, `Record.resolve()`, and `Record.entrypoint()` provide public access. |
| `src/hiraku/base.py` | Generic Markdown/YAML persistence and validation, custom fields, immutable persisted IDs. Do not duplicate serialization in the GUI. |
| `src/hiraku/git.py` | `GitRepository(config).resolve(ref)` resolves a ref to a full commit hash; use for optional record commit input. |
| `src/hiraku/launch.py` | `launch(entrypoint, name=..., args=..., record_id=..., commit=..., dispatch=..., config=..., check=False)` synchronously executes the dispatcher and returns a `CompletedProcess`; `render_arguments()` renders typed flags. |
| `src/hiraku/cli.py` | `record_create` resolves commit refs; `launch_command` resolves effective entrypoints and merges overrides; `dashboard_command` delegates to launcher. Reuse APIs, not Click callbacks or private CLI parsers. |
| `src/hiraku/dashboard/launcher.py` | Loopback server, temporary notebook/cwd, environment context; currently passes `--no-token` to marimo. Interactive mode needs authenticated startup. |
| `src/hiraku/resources/dispatch/launch.sh` | Runs current working tree; accepts but ignores requested commit; reports actual HEAD and clean/dirty state. |
| `src/hiraku/resources/dispatch/worktree.sh` | Requires a commit; executes a detached checkout and removes it on completion; `HIRAKU_ROOT` remains original project root. |
| `src/hiraku/tracking/` | Read adapters, not generic writers. Preserve SQLite snapshot reads and missing-source nonmutation. |

Records have project-wide unique IDs. Root paths are
`hiraku/records/<root>/<root>.md`; child paths are
`hiraku/records/<root>/<child>.md`, regardless of hierarchy depth.
`type` is a free string, not an enum. `commit`, type, status, decision, parent,
and related links are local fields; other fields inherit, with entrypoints
merging by name. Omitted fields inherit; explicit null clears inherited values.
Never create empty local entrypoints/metrics/tracking fields merely because
the user did not fill in the form.

Backend settings control queries; they do not configure the training script's
environment. MLflow scripts must use the matching URI/experiment, and W&B
scripts the matching entity/project. Launch attribution is supplied by Hiraku;
do not allow overriding reserved `HIRAKU_*` variables or fabricate commits.
A successful process can log no run, and a failed process can log a partial
run. Launch status and tracked run status are separate concepts.

## Ticket map

| Ticket | Outcome | Depends on |
| --- | --- | --- |
| HI-00 | Explicit interactive startup and capability boundary | none |
| HI-01 | Validated record creation service | HI-00 |
| HI-02 | Root/child record creation form | HI-01 |
| HI-03 | Background launch controller and worker | HI-00 |
| HI-04 | Entrypoint form, preview, status, output | HI-02, HI-03 |
| HI-05 | Mutation/launch refresh integration | HI-02, HI-04 |
| HI-06 | Integration and browser acceptance | HI-05 |
| HI-07 | Documentation, demo, final checks | HI-06 |

## HI-00 — Explicit interactive mode

Files: `src/hiraku/cli.py`, `src/hiraku/dashboard/launcher.py`,
`src/hiraku/dashboard/app.py`, `tests/dashboard/test_dashboard_launcher.py`,
`tests/test_cli.py`.

- Add `--interactive` to `hiraku dashboard`; default false. Add matching
  keyword-only arguments to launcher helpers, defaulting to false.
- Forward `HIRAKU_DASHBOARD_INTERACTIVE=1` or `0` explicitly, overwriting any
  inherited value so environment leakage cannot enable writes accidentally.
- Create a plain Python capability object shared by mutation/launch services.
  Each service rejects operations when disabled; hiding buttons is insufficient.
- Preserve loopback binding and temporary cache paths. In interactive mode,
  omit `--no-token` and use marimo's supported token authentication; verify
  behavior against the installed 0.25.x CLI. Do not log credentials. Keep
  existing default-mode launcher behavior compatible.
- Reject `--interactive --notebook ...` with a clear error in this increment;
  custom notebook capability contracts are outside scope.
- Display the mode in the header. Interactive controls must also work when
  there are no records or no tracking source; avoid gating them on a fetched
  run snapshot or a selected record.

Acceptance: Click help shows the flag; default service calls cannot write or
launch; explicit mode propagates; custom notebook conflict is tested; initial
render, navigation, refresh, and static HTML export never submit actions.

## HI-01 — Record creation service

New file: `src/hiraku/dashboard/actions.py` (no marimo/pandas imports).
Tests: new `tests/dashboard/test_dashboard_actions.py`.

- Define typed draft/result structures and `create_record(config, draft,
  capability)`; separate pure draft validation/preview from persistence.
- Accept ID, optional parent, related IDs, free-string type, optional commit
  ref, Markdown body, summary/goal/question/hypothesis, and an advanced YAML
  mapping of additional local fields.
- Parse YAML with `yaml.safe_load`; require string mapping keys. Reject
  `id`, `parent_id`, `related_to`, `markdown`, and `path` in advanced fields.
  Reject collisions with populated dedicated fields rather than silently
  overriding. Treat whitespace-only advanced input as an omitted mapping.
- Validate with `Record`/`Entrypoint`/`Metric` and the public persistence API;
  keep tree/path validation there. Resolve a nonempty commit ref through
  `GitRepository.resolve()` before creation. Blank commit stays omitted.
- Preflight with `read_record_index()`. Block mutations on a pending reparent
  journal or malformed/duplicate/tree-invalid records with an actionable
  message; do not invoke implicit recovery from a GUI action.
- Call `Record.create(..., activate=False, config=config)` once on submit.
  Do not update CLI active state or perform a Git commit.
- Recheck references at submission, handle filesystem errors, and return
  field-oriented validation errors without losing the user's draft.

Acceptance: root/child/nested child paths and inheritance round-trip correctly;
duplicate ID, path traversal, missing parent/link, invalid commit, malformed
YAML/entrypoint/metric, and pending journal fail without unintended writes.
Verify `hiraku.yml` bytes and active record stay unchanged after success.
Tests for rejected input compare project files before/after. Existing core
record race semantics remain unchanged; do not claim cross-process transactions.

## HI-02 — Create record form

Files: `src/hiraku/dashboard/app.py`, `src/hiraku/dashboard/theme.css` only
where necessary; HI-01 service. Extend browser coverage in HI-06.

- Add **Create record** action available without selection. Parent defaults
  to selected record; provide a visible **No parent (root)** option.
- Offer basic ID, parent, type, summary and research text fields; expandable
  commit, related IDs, Markdown, and advanced local YAML controls.
- Type suggestions may include task/experiment but must permit custom strings.
  In advanced help, provide copyable entrypoint/metric/backend mapping examples.
- Use a marimo submitted form, not execution driven directly by input values.
  Preview is nonmutating. **Create** persists; **Cancel** clears the draft.
- Deduplicate submitted events by a per-session submission ID. Changing
  selection, graph expansion, form dependencies, or refreshing must never
  replay a successful submission. Disable submit while it is being handled.
- On success show the path, close/reset the draft, select the created ID,
  and request one record/cache refresh through HI-05. Preserve graph search
  and zoom; reveal the new record's ancestors.

Acceptance: user can create root and child records without a terminal, and
invalid forms retain input with readable errors. Browsing and typing write
nothing. Creation selects the new record without changing CLI active context.

## HI-03 — Background launch controller

New files: `src/hiraku/dashboard/jobs.py`, `src/hiraku/dashboard/worker.py`.
Tests: new `tests/dashboard/test_dashboard_jobs.py`.

- Keep `launch()` synchronous and its public signature unchanged. Implement
  a worker module executable with `sys.executable -m hiraku.dashboard.worker`.
  The worker loads project config and calls the public `launch()` API.
- Controller writes a validated JSON request to its own temporary directory,
  outside the project. Start the worker using an argument list, `shell=False`,
  explicit project path, and the dashboard Python interpreter.
- Worker reloads the selected record, obtains its effective named entrypoint,
  and calls `launch(..., name=entrypoint_name, args=overrides,
  record_id=id, dispatch=dispatch, config=config, check=False)`.
  Preserve record-local commit default; optional launch override is explicit.
- Preflight before spawning: capability, journal/index errors, record/name,
  argument types via `render_arguments`, and installed dispatcher existence.
  Revalidate in the worker. Do not bypass launch validation or invoke an
  entrypoint command directly.
- One active launch per dashboard controller; reject concurrent submissions
  and duplicate event IDs. Do not offer a queue in this increment. Identify
  jobs with a UUID, separate from backend run IDs.
- Maintain immutable job views: job ID, record, entrypoint, submitted time,
  starting/running/succeeded/failed state, exit code, and concise failure.
- Redirect worker stdout/stderr to temporary log files, using
  `capture_output=False` inside `launch()` so output is not accumulated in
  RAM. Show bounded tails (64 KiB per stream) as escaped text. State that
  these are launch logs, separate from tracker artifacts.
- Poll process state without blocking the notebook. Terminal transitions
  emit one completion event. Keep at most 20 completed job views/log sets.
- Own jobs at server-process scope so disconnecting/reconnecting a browser
  does not accidentally resubmit. If marimo isolates sessions in separate
  processes, explicitly handle that boundary rather than assuming globals
  serialize every browser; test and document the supported single-user model.
- Implement graceful shutdown: stop accepting launches, wait for active
  worker completion, reap it, then delete logs/requests. Integrate the launcher
  and marimo lifecycle so Ctrl-C does not bypass this drain. If termination
  is needed, signal the owned process tree and let dispatcher cleanup run;
  never kill only the worker and leave training behind. Add platform-specific
  supervision only where necessary and test it. No GUI cancellation button
  until reliable dispatcher/process-tree cancellation exists.

Acceptance: a blocked test worker does not block browsing/polling; duplicate
submission starts one process; success/nonzero exit/startup failure have
distinct results; stdout/stderr tails are bounded and escaped; session
disconnect and server shutdown do not leak worker processes or worktrees.
Use deterministic short subprocess fixtures, no remote credentials.

## HI-04 — Launch form and feedback

Files: `src/hiraku/dashboard/app.py`; actions/jobs services.

- Add **Launch run** panel for selected record. List effective entrypoints
  with defining record IDs; explain no entrypoints and link to creation help.
- Show command tokens and default arguments. Provide YAML mapping overrides
  parsed safely and validated by `Entrypoint`/`render_arguments`; preserve
  booleans, numbers, null and scalar sequences. Overrides affect this launch
  only. Do not parse a free-form shell command.
- Offer installed dispatcher selection (default from config) and optional
  commit-ref override. Reject arbitrary dispatcher paths. Worktree requires a
  resolvable commit; local launch reports that it uses the working tree and
  actual HEAD, even when a record has a commit binding.
- Preview merged argv, dispatcher, record, requested commit and effective
  query source. Do not label a requested commit as actual execution provenance.
  Recompute/revalidate preview on submit; if the record/entrypoint changed
  externally since review, require another review instead of launching stale
  content. Pin the submission to its record despite later navigation.
- Display the logging responsibility and backend/environment distinction.
  Do not silently set MLFLOW/WANDB variables from query configuration.
- Submit once explicitly. Show busy/terminal states, exit code and log tails.
  Poll only job status while running; tracker polling stays off by default.
  Selecting a different record must keep an active job visible.

Acceptance: inherited entrypoint can launch with typed overrides; stored
record remains unchanged; preview matches dispatched argv; bad arguments or
missing entrypoint/dispatcher cannot start a job; failure leaves usable UI.

## HI-05 — Refresh after actions

Files: `src/hiraku/dashboard/session.py`, `src/hiraku/dashboard/app.py`;
tests in `tests/dashboard/test_dashboard_session.py`.

- Add a monotonically increasing action refresh generation or equivalent
  state event. Both explicit Refresh and a successful create invalidate
  record/source caches using the existing session refresh path.
- Job completion, including failure, requests exactly one fresh snapshot for
  the user's current record/scope. A failed script may still have logged data.
  Status polls, log-tail polls and form edits must not query trackers.
- Preserve current graph state, filters, scope, sort and valid run selection.
  Preserve selected record if the user navigated during training. Do not
  automatically switch to the job's record on completion.
- Keep last-successful data with STALE labels on fetch failure and existing
  partial-subtree handling. Do not turn tracker visibility failure into job
  failure or roll back a created record.
- Never equate a job UUID with a tracker run ID. Show “Execution completed;
  refresh results to inspect logged runs” and an **Open record** action.
  If no new run is visible, explain no logging/delayed syncing/wrong source,
  without fabricating a row or claiming definite job-to-run correlation.

Acceptance: one source query per refresh generation, zero tracker calls for
status-only polling, one completion refresh per job, no action replay, and
correct stale behavior when a backend fails after successful execution.

## HI-06 — End-to-end acceptance

Files: `tests/dashboard/fixtures.py`,
`tests/dashboard/test_dashboard_acceptance.py`,
`tests/dashboard/test_dashboard_browser.py`, new actions/jobs unit tests.

- Build a temporary Git/Hiraku project using the bundled minimal example.
  Create a task with `val/accuracy` maximize, then a child with evaluate
  command `[uv, run, python, evaluate.py]` and default seed 0.
- Browser test in interactive mode creates records, submits evaluate with
  seed 1, waits for completion and observes one newly logged CSV row, matching
  record ID, entrypoint, actual commit/git state and `param.seed`.
- Assert run appears in record/subtree scope and plot data after refresh.
  Repeat intentional launch to create a second unique backend run; changing
  scope, sorting and refreshing must not create a third.
- Test local dispatcher and worktree dispatcher attribution with code committed
  before creating the records; ensure output writes to original root and the
  temporary checkout is cleaned up. Add a no-logging success and nonzero-exit
  fixture to cover the launch/run distinction.
- Add short local MLflow integration using native SDK logging (no server or
  training download); mock W&B launch/query boundaries. Do not require live
  W&B credentials. Keep original read-only browser file comparisons intact.
- Test browsing interactive mode without submissions is nonmutating; default
  mode has no enabled write controls. Exercise auth in browser startup using
  the supported marimo token URL. Keyboard labels, error focus and narrow
  layout must remain usable.
- Exercise shutdown with a running short fixture and a worktree, including
  the launcher interrupt path; verify no orphan subprocess or checkout.

Acceptance: all existing tests still pass; new tests demonstrate outcomes,
not only internal mock calls. Browser tests remain opt-in with the existing
`HIRAKU_TEST_BROWSER` convention.

## HI-07 — Docs, demo and release readiness

Files: `docs/dashboard.md`, `scripts/test_dashboard.py`,
`scripts/README-dashboard.md`, `tests/test_dashboard_demo.py`, `CHANGELOG.md`.
Update Python API docs if any public API changes beyond dashboard helpers.

- Rewrite the dashboard introduction: default read-only, explicit interactive
  mode for creation/launches. Qualify the existing read-only guarantee without
  weakening its default-mode tests or SQLite query isolation.
- Document flags/authentication, fields/inheritance, logging ownership,
  launch output/status versus backend runs, dispatcher/commit behavior,
  delayed tracking visibility, single-user/concurrency limits, shutdown drain,
  and temporary log retention. No claim of resumable durable jobs.
- Add a full minimal CSV walkthrough and a Digits walkthrough creating a
  child with a train entrypoint and launching it. Preserve comparison metrics
  `val/log_loss` minimize and `val/accuracy` maximize. Use a short epoch budget
  in automated tests; users may run the existing 40-epoch demo manually.
- Add `--interactive` forwarding to demo creation/reopen paths; default remains
  read-only. Document how to reopen a retained demo in interactive mode.
- Run `uv sync --all-extras`, install pre-commit hooks, and perform focused
  actions/jobs/session tests while implementing. Final required checks:

  ```bash
  uv run --all-extras pytest
  uv run --all-extras ruff check .
  uv run --all-extras marimo check src/hiraku/dashboard/app.py
  uv run --all-extras zensical build --clean
  uv run --all-extras python scripts/check_changelog.py
  ```

- Run opt-in browser acceptance when a local browser/driver is available:
  `HIRAKU_TEST_BROWSER=chrome uv run --all-extras pytest tests/dashboard/test_dashboard_browser.py -n 0`.
  Report unavailable browser checks explicitly. Run `uv build` and an installed
  wheel worker/dashboard smoke test if packaging/dependencies are changed.
- Review complete diff; never commit `site/`, `dist/`, `.reasonix/`, logo
  archives, caches, job request files or execution logs. Record validation
  results and known limitations in the final implementation handoff.

## Definition of done

A user can create root/child records and launch their logged evaluations from
the bundled dashboard, see responsive execution feedback and refreshed results,
and retain correct record/entrypoint/commit attribution. Default browsing stays
read-only. No reactive re-execution can replay actions. Core CLI and tracking
contracts stay compatible. Tests, example walkthroughs and documentation agree
with the final behavior, including failure and shutdown paths.
