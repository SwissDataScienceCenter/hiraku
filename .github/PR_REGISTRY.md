# PR — Dataset registry

> Stacked on `integration/snakemake-hiraku-main` (`9301292`, the Snakemake
> integration PR). Rebase onto `origin/main` once that PR merges:
> `git rebase --onto origin/main 9301292 feat/hiraku-registry`.

## Title

`feat(registry): register existing datasets and their artifacts as records`

## Body

Add a generic Hiraku API and CLI for registering existing datasets and their
artifact descriptors as records. Registration observes data and documents its
source; it never executes workflows, fabricates runs, copies dataset contents,
computes scientific metrics, or infers historical production provenance.

- **Schema** — one namespaced, versioned front-matter key (`registration:
  {schema: 1}`) on ordinary records, so main's Hiraku keeps reading registered
  projects. Deterministic `(namespace, key)` identity, canonical content
  digests that exclude observation times, and `present | missing | unknown |
  stale` availability.
- **API/CLI** — `hiraku registry import MANIFEST` and `hiraku registry scan`,
  previewing by default and applying explicitly with a reviewed hash.
- **Persistence** — field-owned, idempotent writes that preserve human Markdown
  and links, report source-ownership conflicts, and mark disappeared artifacts
  stale under exhaustive scans without deleting records.
- **Dashboard** — a Datasets view built from records' registration fields,
  linked to workflow runs only by exact normalized path.
- **Example** — `datasets-registry` walks preview → register → browse →
  unchanged reimport → changed revision, including descriptor-only remote data.
- **Cohort adapter** — the Flagship `tp-agent registry import` command now
  emits this generic manifest and writes through the public API. SOP evidence
  keeps `role: unknown` and `applicability: unknown`, and legacy cohort records
  migrate in place under a collection parent without duplicating datasets.

## Base and revisions

- Base commit: `9301292` on `integration/snakemake-hiraku-main` (worktree
  `worktrees/integration`).
- Registry branch: `feat/hiraku-registry` (worktree `worktrees/registry`).
- Commits: `b82feb8` (schema), `71af3bd` (review API/CLI), `62aa85b`
  (persistence/refresh), `d4a298e` (discovery), `8ef0487` (dashboard),
  `c887d55` (example/docs/handoff), `a79364e` (canonical order and legacy
  parent migration), and `4f9c56d` (legacy evidence roles).
- Flagship adapter: `/Users/daniel/git/flaghsip-hariku/tp-agents`, files
  `src/registry_manifest.py`, `src/registry_writer.py`, `src/registry_cli.py`,
  `src/tp_agent_cli.py`, `tests/test_registry_generic.py`,
  `docs/registry-import.md`, and `pyproject.toml`. Its commit is pending in
  that repository; the Hiraku PR does not depend on it.

## Checks run

- `uv run pytest` — 552 passed, 8 skipped (dashboard, registration, CLI).
- `uv run ruff check .` — all checks passed.
- `uv run zensical build --clean`.
- `uv build`.
- `uv run python scripts/check_changelog.py`.
- Flagship: `tests/test_registry_generic.py` + `tests/test_registry_manifest.py`
  + `tests/test_cli.py` — 27 passed; Ruff on the touched files passed.

## Migration guidance

Legacy cohort records (the Flagship writer's `registry`, `files`,
`missing_expected`, `related_to`, and `importer_fields` fields) and the earlier
`inventory_files` / `file_inventory` shape are mapped by
`hiraku.registration.compat`. Reimporting them preserves deterministic record
IDs and human text and never duplicates a dataset.

## Remaining validation

- **Live remote pilot** — an operator validation step with separately supplied
  access, before any production cohort import. Run `--dry-run`, review the
  manifest counts and SOP coverage, back up the Hiraku project, then apply one
  technology before expanding scope.
- **Flagship commit** — the adapter changes are tested but were made in a
  sandbox that could not write the outer repository's git metadata; commit them
  in that repository before the live pilot.
