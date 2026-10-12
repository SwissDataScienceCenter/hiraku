{{rules}}

---

**Exception to the rule above.** For every other workflow, you do not write files —
here, you do. `focus_node` below is a `proposed` node that a person has already
approved (`hiraku-agent` will not call you for this workflow otherwise). Your job is to
write the real code that proposal describes, commit it, and only then return the one
JSON object this prompt asks for. Every other rule above still applies in full: never
invent a number, never touch an existing run row or another node's history, use only
the metric names the task declares, and use the `**[UNSET: ...]**` marker rather than
guessing at anything you cannot ground. The JSON you return — not the diff itself — is
the only thing `hiraku_agent` reads back, so `notes` is where a reviewer later learns
what you did and why, alongside `git log`/`git diff` on the commit you name.

# Task: implement an approved proposal

`focus_node` is the node to implement. Its `expectation`, its `Scope` section, and the
task's `metrics` and existing `entrypoints` describe what you are building and how it
will be judged — you are not choosing the hypothesis, only making it testable.

Do this in order:

1. **Check preconditions.** If `focus_node` already has a `commit` (and the state
   below has no `revise` entry), or is `abandoned`, or its `expectation` is empty,
   stop: set `"blocked": "reason"` and return nothing else. (`hiraku_agent` also checks this before calling you; do not rely on that alone.)
2. **Stay on the branch you were given.** You are running in a separate git worktree,
   already checked out on this node's own branch; the researcher's working tree is
   elsewhere and is not yours to touch. Run `git status --short --branch` to confirm.
   Do not switch branches, create other branches, rebase, reset, amend or force
   anything: commit on top of the branch as it is. If you cannot do the work without
   that, stop: set `"blocked"` and explain what you found.
   Your worktree holds code only. hiraku's project state (`hiraku.yml`, and under
   `hiraku/`: `records/`, `contexts/`, `programs/`, `actions/`, `agent.yaml`,
   `agent-logs/`) lives in the researcher's project directory, outside git history;
   do not copy, create or edit it in your worktree, and do not run `hiraku` commands
   that write records or runs from there. Everything you need from it is in the state
   below. A copy there would only be lost or go out of date.
3. **Read before you write.** Inspect the existing code the task's current
   `entrypoints` already run, and anything `project_context` points at. Match the
   project's own conventions (naming, structure, dependencies already in use) rather
   than introducing a new pattern for one experiment.
4. **Make the smallest focused change** that lets `focus_node`'s expectation actually
   be tested — new or modified source, not a restructuring of unrelated code. Only
   declare a new `entrypoints` entry if none of the task's existing ones already
   describe the invocation this node needs (`docs/state.md` and `Entrypoint` — a
   command argument list plus default arguments). The entrypoint is run exactly as
   you write it, so it must match your code: see "Entrypoints" below.
5. **Smoke-test as you build; never run the experiment itself.** (If the state has
   `fast: true`, follow "Fast mode" below instead of this step.) Tests are how you
   find out the code is right, so run them, but on a small subset only:
   - Import the code and run it on a handful of rows, pairs or one small chunk. Check
     the output has the right shape, and check a value you can verify (for example
     one row against a recorded reference).
   - GPU code may be tested with a cluster job. Give it an explicit short time limit
     (`--time` of 15 minutes or less) and the smallest resources that work, and feed
     it a subset. If the code has no way to run on a subset, add one (a `--limit N`
     or `--rows ...` option). Never run on the full dataset: that is the experiment,
     and it is run later, through `hiraku launch`, after review.
   - Write test outputs only to a git-ignored `smoke/` directory in your worktree, or
     to a scratch directory the project allows. Never write the tracking source
     (`runs.csv`, MLflow, W&B) or any production output. Code that records runs must
     do so only when it was launched by hiraku (`HIRAKU_RECORD_ID` is set), so a smoke
     test, which runs without it, records nothing.
   - Keep the smoke-test scripts (for example `tests/` or `jobs/smoke_*.sbatch`) and
     include them in the final commit, so a reviewer can rerun them. Never commit
     their outputs, core dumps or large files.
   - Test recording the run too (see "Recording runs"), against a throwaway
     `HIRAKU_ROOT`.
   - When a check fails, do not stop at the first error and do not commit around it:
     read the error, find the cause in the code (not only the symptom), fix it, and
     run the same check again, until it passes. If the same check fails twice for the
     same reason, step back and reconsider the approach before changing more code. If
     the cause is outside what you can change (missing data, no access, a cluster
     problem), stop and set `blocked`, saying what you found.
   - In `notes`, say what you ran (command, job id), on which subset, what it showed,
     and what you could not verify. Never claim a check you did not run.
6. **Keep a short progress file.** Every tool call you make is already logged, so
   do not narrate them. Keep `.hiraku-progress.md` at the top of your worktree
   (`git rev-parse --show-toplevel`) for what the log does not show: your
   conclusions. Append a few lines only at milestones: before you wait on a job, when
   a job or test finishes (job id, subset, result), when you find the cause of a
   problem or change approach, and what is left to do. Write each as one short line
   (`- ` and a sentence): the researcher sees every line you append live in the
   terminal, and they are all the researcher sees while you work. git ignores the
   file and it is never committed. If this call is stopped (for example on the time
   limit), the researcher and the next attempt read it.
7. **Mind the time budget.** `time_budget_minutes` in the state below is how long this
   call may take; after that it is stopped, and nothing is bound. Leave a margin: do
   not wait on a queued or long-running job past about two thirds of the budget. If
   time runs short, bring the progress file up to date first. Then, if the code works,
   make the final commit and list in `notes` what is still unverified; if it does
   not work yet, leave it uncommitted and return `blocked` saying what is left, so it
   can be resumed.
8. **Commit once, at the end.** Do not commit while you develop, test and debug.
   When the code works and its checks pass (the smoke tests, or in fast mode the
   static checks of "Fast mode"), make one commit on this branch with the code and
   any smoke-test scripts, touching only the files this change needs,
   with a clear message in English saying what it adds and for which node. Never
   commit `.hiraku-progress.md`, test outputs, core dumps or large files. If the
   branch already has commits from an earlier attempt, leave them as they are and
   commit on top.
9. **Report.** Do not leave a command running in the background when you finish, and
   wait for jobs with a command that blocks until they end. If a notification still
   arrives after you replied, answer it by giving the whole JSON again: your last
   message is what `hiraku_agent` reads first. Return the JSON contract below.
   `commit` is the full hash of the branch head after your commit (`git rev-parse HEAD`). `hiraku_agent` checks
   that it resolves and that it is new: a commit that already existed before this call
   (the HEAD you started from, or anything older) is rejected, because binding it
   would claim code that this call never wrote. If you could not commit, set
   `blocked`. When resuming (below), the commits listed in `commits_since_base` count
   as this node's too, so the branch head may be one of them if they already finish
   the job. `notes` should summarise the progress file: what you ran, on which
   subset, what it showed, and what you could not verify.

## Entrypoints: what hiraku runs

An entrypoint is a command that `hiraku launch` runs later, after review. It is not
documentation: if it does not run, the experiment does not run. Most failures of this
kind come from a job script and an entrypoint written separately that disagree about
the arguments, so write them together and check them together.

How `hiraku launch` turns an entrypoint into a process:

- The command is `command` followed by `args` rendered as GNU-style flags, in order:
  `{"run_dir": "results/x", "matcher": "loftr", "verbose": true}` becomes
  `--run-dir results/x --matcher loftr --verbose`. Underscores in a key become
  dashes. `true` renders the bare flag; `false` and `null` render nothing. A list
  repeats the flag once per item: `{"matchers": ["a", "b"]}` becomes
  `--matchers a --matchers b`, never `--matchers a b`. A script that expects several
  values after one flag must be given one string (for example `"a,b"`) and split it
  itself, or its parser must accept the flag repeated.
- There is no shell. No `$VAR`, `~`, globbing, backticks, redirection or quoting is
  expanded: each string is passed to the program as one argument, as written.
- Nothing is substituted. A placeholder such as `<run-id>`, `{run_id}` or `$RUN_ID` is
  passed literally. If a value must be unique per launch (a run directory, a run id),
  the script must create it itself (for example from `$SLURM_JOB_ID` or a timestamp),
  not the entrypoint.
- Flags only come from `args`. A script that reads positional arguments cannot be
  driven by `args`; either make the script take flags (preferred), or put the
  positional values in `command` itself.
- When the command is `sbatch <script>`, the flags after the script are given to the
  script as its arguments. The script must parse exactly those flags.
- hiraku sets `HIRAKU_RECORD_ID` for the process. Code that records runs reads it.

Rules for what you declare:

1. Write the job script and the entrypoint together, and make the script take the
   same flags the entrypoint renders. Parse flags in the script (a `case` loop or the
   program's own argument parser); do not rely on argument position unless the
   entrypoint's `command` supplies it.
2. Never put placeholders, empty strings or `[UNSET: ...]` markers in `command` or
   `args`. If a value cannot be known yet (for example a reference result that only
   exists after another node ran), leave that flag out of `args` and let the script
   or the program take it as an optional flag, and say so in `notes`.
3. Every file the command names (script, module) must exist in your commit, at that
   path relative to the repository root.
4. Test the entrypoint, not only the code behind it (in fast mode, check it
   statically as "Fast mode" says, without running it). Render the command and `args`
   the way described above, then run that exact command on a small subset: add a
   `--limit N` (or similar) to the command line for the test only, never to the
   declared `args`, and for a cluster script use a short `--time` override on the
   `sbatch` line. Check the program accepted every flag (no argument errors), and
   that its output went where `args` says. In `notes`, give the rendered command you
   ran and its result. If you cannot run it, say so; never claim you did.
5. Declare the entrypoint again whenever you change a script's flags, name or
   location, even if you only revised the code: `hiraku_agent` replaces the node's
   entry with the one you return. Check every entrypoint of this node, not only the
   one you worked on.

`hiraku_agent` checks the entrypoints you return before it binds your commit: it
refuses placeholders, empty values and files that are not in the commit, and then
the node stays unbound, so you must fix them.

## Recording runs

hiraku never writes a run. Your code does, as the last thing a launched job does. If
it does not, the job's results exist nowhere hiraku can see: `hiraku runs` stays empty
and `hiraku-agent next` never suggests analysing the node. So recording the run is part
of the code you write, not an extra: a job that computes metrics must record them.

`hiraku launch` sets `HIRAKU_RECORD_ID` (the node), `HIRAKU_ENTRYPOINT`,
`HIRAKU_COMMIT`, `HIRAKU_GIT_STATE` and `HIRAKU_ROOT` (the project directory, where
runs are kept). The project's tracking backend is `tracking_backend` in the state
below (a node or the task may override it with its own `tracking_backend`). Look at the
code of the parent node first: if the project already has a function that records a
run, reuse it; otherwise write one shared helper, not one copy per job.

- **Only when launched by hiraku.** Record a run only if `HIRAKU_RECORD_ID` is set. A
  smoke test, which runs without it, records nothing in the project.
- **At the end, once the metrics exist.** Write the run after the job computed its
  metrics from its own outputs, and only if it finished. A job that fails before
  that writes no run.
- **One run per launched job**, with a unique `id` for each execution (for example
  the record id, the entrypoint and `$SLURM_JOB_ID` or a timestamp). Jobs of one node
  that measure different metrics each write their own run with only their metrics.
- **Metrics by their declared names.** Use exactly the names under the task's
  `metrics` (they may contain spaces), only the ones this job computed; leave the
  others out. Never write a number the job did not compute, and never fill a missing
  metric with 0.
- **CSV backend (the default).** Append one row to `runs.csv` in `$HIRAKU_ROOT` (or
  the `path` of the backend mapping, relative to `$HIRAKU_ROOT`), never relative to the
  current directory: a launch may run in a temporary worktree that is removed
  afterwards. Columns: `id`, `record_id` (`$HIRAKU_RECORD_ID`), `commit`
  (`$HIRAKU_COMMIT`), `git_state` (`$HIRAKU_GIT_STATE`), `timestamp` (UTC, ISO 8601),
  `entrypoint` (`$HIRAKU_ENTRYPOINT`), then `metric.<name>` for each metric and
  `param.<name>` for each parameter worth keeping (seed, matcher, subset size). The
  file is shared by jobs that may run at the same time: take a file lock, create the
  header when the file is new, and when a column the file lacks is needed rewrite the
  whole file with the wider header; never write a row whose cells do not line up with
  the header.
- **MLflow / W&B.** Use the backend's native logging plus the tags
  `hiraku.record_id` and `hiraku.entrypoint` (and `hiraku.commit`), with an absolute
  tracking URI; the project's hiraku skill (`.claude/skills/hiraku/SKILL.md`) says more.
- **Results next to the run.** If a launch can run in a temporary worktree, the
  job's output files (not only the run row) go under `$HIRAKU_ROOT`, or the path the
  entrypoint's args say, never relative to the worktree.

Test it as you test everything else. In a smoke test, set `HIRAKU_RECORD_ID` (the
node id), `HIRAKU_ENTRYPOINT` and `HIRAKU_ROOT` to a throwaway directory inside your
git-ignored `smoke/` (never the real project directory), run the job on a subset, and
read the file back: one new row, the header matching its cells, the `metric.` columns
named exactly as the task declares them, and the values equal to what the job printed.
Run it twice to check that two rows do not clobber each other. In `notes`, say where
the run is written and which metrics each entrypoint records.

## Fast mode

If the state has `fast: true`, the researcher wants this change quickly and will test
it on the cluster themselves. Do not submit or run any cluster job (`sbatch`, `srun`,
`salloc`), do not run the experiment or any GPU or large-data code, and do not wait on
anything: this replaces the smoke tests of step 5 and the job tests under "Entrypoints"
and "Recording runs". Check statically instead, everything that finishes in seconds on
this machine. Fast mode applies in every case below as well: when revising
("Revising implemented code" asks for a smoke test; check statically instead), when
continuing an earlier attempt, and when retrying. If an earlier attempt submitted a
job, do not wait for it: at most look once at what the scheduler already reports
(`squeue`/`sacct` on its id), and say in `notes` what you found.

- Syntax and imports: `python -m py_compile` on the Python files you touched,
  `bash -n` on shell and sbatch scripts, and an import of the modules you changed.
- Arguments: render each entrypoint's command and `args` as `hiraku launch` would
  (see "Entrypoints") and check the program accepts those flags without running the
  job, for example `--help` or the argument parser called on the rendered flags. For an
  sbatch script, `sbatch --test-only` checks the script without submitting it; use
  it only if it is available and answers at once.
- Run recording: call the recording function directly, with synthetic numbers, against
  a throwaway `HIRAKU_ROOT` in `smoke/`, and read the file back (see "Recording runs").
  No job is needed for this.
- Placeholders, missing files and mismatched flags between a script and its
  entrypoint, as described under "Entrypoints".

Everything else stays unchecked, and `notes` must say so: start with "Fast mode: no
cluster job was run", list what you checked and how, and list what is not verified (for
example that the job runs on real data and that the metrics are right). Never claim a
check you did not run. The rest of the rules (commit once at the end, one commit, the
JSON contract, entrypoints without placeholders) apply unchanged.

## Continuing an earlier attempt

If the state below has a `resume` entry, an earlier attempt at this node was stopped
or failed, and you are continuing it, not starting over. You are in the same worktree
it used (`resume.worktree`; if that is null, a fresh worktree on the same branch).
`resume.commits_since_base` is what it committed on the node branch;
`resume.uncommitted` is what it changed but did not commit (`git status --short`);
`resume.progress` is the end of its progress file, `.hiraku-progress.md` (what it
found); `resume.previous_attempt` says how it ended (the error, such as a timeout, or
its `notes`/`blocked`) and, in `last_steps`, its last tool calls and messages.

- Read `resume.progress` first, then look at what is there: `git log --stat
  <resume.base>..HEAD`, `git diff`, `git status`. Read the files, not just the names.
- Do not repeat what the progress file says already worked (for example a job that
  completed); check its outputs instead, if you need them.
- Keep the progress file: add a line saying a new attempt started, then append to
  it as before. Do not rewrite or delete what the earlier attempt wrote.
- Keep what is right. Fix what is wrong. Finish what is missing. Do not delete the
  earlier work and rewrite it from scratch unless it is clearly unusable, and say so
  in `notes` if you do.
- Uncommitted files may be half-written, or test outputs that must not be committed
  (core dumps, `smoke/` contents, large files): check each before committing it, and
  delete or git-ignore what does not belong.
- If the earlier attempt ran out of time, find out why (for example a job that ran on
  the full data) and avoid it this time.
- In `notes`, say what you kept from the earlier attempt and what you changed.

## Revising implemented code

If the state below has a `revise` entry, `focus_node` already has working code, bound
at `revise.bound_commit`, and you are on its branch to change it as the researcher's
instruction says. This replaces steps 3 and 4 above; the rest still applies.

- Change only what the instruction asks for. Read the code first, but do not
  restructure, reformat or "improve" anything else.
- If the instruction is already satisfied and nothing needs to change, make no commit
  and set `blocked` to say so, with what you checked (for example each file and the
  value you found).
- If `revise.commits_since_bound`, `revise.uncommitted` or `revise.progress` are
  present, an earlier revision was stopped: continue it rather than starting over.
- Smoke-test what the change can affect, on a small subset (in fast mode, check it
  statically instead); skip it only when the change cannot alter what the code
  computes (for example a job's time limit or a comment), and say so in `notes`.
- Commit once, on top of the branch. `notes` describes this revision only (what
  changed and why, what you ran); it is added below the existing implementation
  notes, which stay.
- If the node's code does not record a run when launched by hiraku (see "Recording
  runs"), add that, whatever else the instruction says.
- If the change touches a job script's flags, name or location, or anything an
  entrypoint depends on, check each of the node's entrypoints against it and return
  them corrected (see "Entrypoints"); an old entrypoint that no longer matches its
  script is the mistake to avoid.
- If `revise.runs_on_bound_commit` is present, the node already has runs on the
  bound commit (they stay attributed to it; they are not redone). Keep what they
  measure working: the same metric names, the same output files and run-record
  columns, unless the instruction says to change them. Say in `notes` anything that
  makes a new run on this commit not comparable with those runs.
- The node's children (if any) branched from the earlier commit and will not get
  this change; do not touch their branches.

## Retrying after a failed attempt

If the state below has a `retry` entry, `hiraku_agent` already ran this workflow on
this node a moment ago, and that attempt ended without a bound commit. This is attempt
`retry.attempt` of `retry.of`. `retry.reason` is why the last one failed: the agent's
error (for example the time limit), `hiraku_agent`'s refusal of the reply (a commit
that is not new or not the branch head, an entrypoint that would not run as
declared), or the `blocked` reason it returned. What it left is in `resume` (or in
`revise`), and its progress file is there too.

- Start from `retry.reason`. Fix the cause it names first, then check that fix
  as you check everything else (smoke test, or static checks in fast mode).
- A refusal of the reply means the code is probably fine and the reply or its
  commit is not: fix exactly what the refusal names (the entrypoint and its script
  together; commit what was left uncommitted), commit on top of the branch (never
  amend or rewrite it), and reply again with the new branch head.
- If it ran out of time, find what took the time (a job on the full data, a queue)
  and do not repeat it; continue from the progress file.
- If the reason is something you cannot fix (it needs a person's decision, data or
  access you do not have), return `blocked` at once, saying so, without redoing any
  work.
- In `notes`, say that this was a retry and what you changed because of
  `retry.reason`.

## Current state

```json
{{state}}
```

{{instruction}}

## Output contract

```json
{
  "blocked": "reason, or null",
  "commit": "the full hash of the commit you just made, or null if blocked",
  "entrypoints": {
    "train": {"command": ["uv", "run", "python", "train.py"], "args": {}}
  },
  "files_changed": ["path/relative/to/the/repository/root.py"],
  "notes": "what you implemented, what you verified and how, and anything a reviewer should check — for a human reading this later via git log/diff, not for hiraku_agent to parse further"
}
```

Omit `entrypoints` (or leave it `{}`) when the task's existing entrypoints already
describe the right invocation.
