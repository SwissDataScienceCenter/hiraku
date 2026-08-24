# Environment capture and running experiments in parallel

Two separate problems that get conflated. *Capture* is about being able to explain a number later.
*Separation* is about several experiments existing at once without corrupting each other. The
existing implementation already solves most of the first and half of the second.

## What is already true

`hiraku launch` distinguishes two forms, and the distinction is the backbone of everything here:

- **Working-tree launch** — runs the current checkout including uncommitted and untracked changes.
  Fast, and correct for debugging. Marked **non-reproducible**; the code that ran is not recoverable.
- **`--commit <ref>` launch** — resolves the ref to a full SHA and runs that snapshot in a temporary
  detached worktree, leaving the active checkout alone. **Reproducible.**

`launch` injects `HIRAKU_EXPERIMENT_ID`, `HIRAKU_GIT_COMMIT`, `HIRAKU_TRACKING_BACKEND`, and an
absolute `HIRAKU_TABLE_PATH`, and project code writes the row.

## Capture: the reproduce-minimum set

The aim is not a bit-exact environment archive. It is the smallest set of facts that lets someone
six months later either reproduce the number or explain precisely why they cannot. Anything beyond
that costs storage and review attention on every single run and is rarely read.

| Column | Example | Why |
|---|---|---|
| `commit` | `a3f19c…` | Existing. What code ran. |
| `run_kind` | `commit` / `worktree` | Whether that answer is trustworthy. |
| `reproducible` | `true` / `false` | Derived from `run_kind`; the promotion gate reads it. |
| `entrypoint` | `verify` | Which `launch.yml` command, so args are recoverable from the manifest at that commit. |
| `seed` | `1` | The single biggest source of unexplained variance between two runs of the same code. |
| `python_version` | `3.14.0` | Cheap, and explains a surprising share of "it worked yesterday". |
| `lockfile_sha256` | `9f2c…` | Identity of the resolved dependency set without storing it. Two runs sharing this hash had the same dependencies; differing hashes point at what to diff. |
| `platform` | `Linux-6.8.0-x86_64` | Kernel and architecture. |
| `accelerator` | `1x NVIDIA A100 40GB` | Non-determinism and throughput both depend on it. One string, not a hardware inventory. |
| `launched_at` | ISO-8601 UTC | Ordering, and correlating with anything outside the project. |

Reasoning behind the two least obvious choices:

**A lockfile hash rather than a dependency export.** The export answers "what exactly was
installed"; the hash answers "was it the same as that other run", which is the question actually
asked, at a few bytes per row instead of hundreds of lines. The full resolution is already in
`uv.lock` at the recorded commit — recoverable for a commit-backed run, which is precisely the class
of run allowed to be promoted. Storing the export as well would duplicate versioned content into an
append-only table.

**A `worktree` run records `commit` as its base only.** Tempting to hash the diff instead, but a
recorded hash of an unrecoverable diff invites false confidence. `reproducible: false` is the honest
signal, and the promotion gate refuses those runs outright.

### Whose job is writing these

Today project code writes the row, so the example `train.py` reads the `HIRAKU_*` variables. That
does not scale to a shared convention: every project would reimplement the same provenance columns
slightly differently, and the columns the promotion gate depends on would be unenforceable.

Proposal: `launch` injects the provenance values it already knows —
`HIRAKU_RUN_ID`, `HIRAKU_RUN_KIND`, `HIRAKU_REPRODUCIBLE`, `HIRAKU_ENTRYPOINT`,
`HIRAKU_LOCKFILE_SHA256`, `HIRAKU_LAUNCHED_AT` — alongside the existing four, and the documented
contract is that project code copies them into its row verbatim and adds its own metrics. `launch`
still does not verify that a row was written; that stays out of scope. This keeps the CLI a thin
wrapper while making the columns predictable, which is what the promotion gate and the dashboard
both need. `seed`, `python_version`, `platform`, and `accelerator` stay the project's to report,
since only it knows what governs its own run.

## Separation: worktrees, one per active experiment

The tree makes several experiments active at once by design — the parallel A/B/C/D case. A single checkout cannot serve that: switching branches to launch a sibling
disturbs a run reading files from disk, and two long runs cannot proceed at all.

`git worktree` is the mechanism, and it is already in the codebase for `--commit` launches. The same
primitive used deliberately and durably:

```bash
git worktree add .worktrees/geometric-verification exp/geometric-verification
git worktree add .worktrees/vggt-pose-consistency   exp/vggt-pose-consistency
```

Each active experiment gets its own directory on the same repository — one object store, no second
clone, each with its own checkout, its own uncommitted state, and its own virtual environment. Two
experiments can then hold conflicting dependency versions, which is the practical reason a shared
checkout fails on real work. `.worktrees/` belongs in `.gitignore`; the branches are the shared
artefact, not the directories.

`launch` needs no change to support this — it is run from inside a worktree and finds the project by
searching upward for `.hiraku.yml`, exactly as it does now. The run table is at the project root, so
rows from every worktree land in one table, which is what makes the leaderboard work across parallel
experiments. Concurrent appends to one CSV are the one real hazard; a row is small and appended in a
single write, so the practical fix is a lock around the append, and this should be stated as a
requirement rather than left to luck.

What is worth adding to the CLI is only the bookkeeping: a command that creates a worktree for an
experiment, records its path, and removes it on conclusion. The alternative — leaving it to the
researcher — means the tool's own parallelism story is a paragraph in a README.

## `research/config.md`: the project contract

One file, at the top of `research/`, holding what applies across goals: the environment the project
runs in, the bounds an auto loop must respect, and the promotion tolerance. The per-experiment
`policy.md` stays authoritative for that node and may only narrow what is here.

Sections: **Goals** (active goal IDs), **Environment** (how to build it, hardware assumptions,
`accelerator` string convention, whether runs are expected deterministic), **Bounds** (max runs per
goal, max tree depth, max concurrent worktrees, wall-clock and cost ceilings), **Promotion**
(reproduction seeds required, agreement tolerance, who signs off per mode), **Data** (paths,
checksums, what may not leave the machine).

It is markdown rather than YAML because most of it is prose a reviewer reads — the reasoning behind
a bound matters as much as the number. The few values a tool needs go in front matter.
