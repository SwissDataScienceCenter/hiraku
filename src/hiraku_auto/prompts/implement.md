{{rules}}

---

# Task: implement one experiment

You are writing code this time, not returning records. The brief below is the specification: an
expectation to be tested, a frozen evaluation protocol, and the run contract the code must satisfy.

## What you may and may not touch

- **Write code** in the working directory you were started in, and only there. It is a checkout of
  this experiment's branch, so every file you write is on that branch and nothing lands on the
  default branch.
- **Write `launch.yml`** at the absolute path named in the brief. Without it the experiment cannot
  be launched, so this is not optional. Declare one entrypoint per thing that is actually meant to
  be run, with a command that works from the `cwd` you declare.
- **Never edit a record** — nothing under `research/`, no `experiment.md`, no goal. If the record is
  wrong, say so in `blocked` and stop. Editing the expectation to match what you built is the one
  thing that makes the whole loop worthless.
- **Never change the evaluation protocol**, and never work around it. If it cannot be implemented as
  written, that is a finding: report it in `blocked`, with the specific line that cannot be met.

## The run contract is the part that is easy to get wrong

The row `launch` records is the only evidence the promotion gate ever sees. So:

- Metrics go to `$HIRAKU_METRICS_PATH` as one flat JSON object of numbers. Keys are the metric names
  the goal's protocol defines — no others, no renamings, no nesting.
- Every source of randomness is seeded from `$HIRAKU_SEED`. A run whose seed does nothing cannot be
  reproduced at another seed, and reproduction is a promotion condition.
- Failure exits non-zero. Exiting zero having computed nothing is recorded as an execution defect
  and wastes a whole cycle of the loop.
- Do not write results into the run table yourself, and do not invent extra columns. The tool
  records the run.

## Before you write anything

Read enough of the surrounding code to fit it: how data is loaded, how the existing pipeline is
invoked, what is already implemented that you should call rather than reimplement. An experiment
whose code duplicates the project's own pipeline is measuring your reimplementation, not the
project's method.

Where the brief carries an `**[UNSET: ...]**` value, or where the protocol needs something the code
cannot obtain (a path that does not exist, a manifest that has not been built), **stop and report
it**. Do not choose a plausible value to make the code run — a fabricated dataset path produces a
number that looks like a result.

If the experiment depends on an input that does not exist yet — a frozen pair list, a baseline
artefact — say so in `blocked` rather than generating a substitute.

## Keep it runnable and small

Prefer one entrypoint module that composes existing code over a new framework. Add a test only where
it pins behaviour the protocol depends on. Leave the working tree in a state where the declared
command actually runs; if you cannot verify that (no data locally, no GPU), say precisely what you
could not check under `unverified`.

{{brief}}

## Output contract

Write the code first, then return ONE JSON object describing what you did:

```json
{
  "files": ["paths you created or modified, relative to the working directory"],
  "entrypoint": "the entrypoint name you declared in launch.yml, or null if you were blocked",
  "command": "the command line that entrypoint runs, as one string, for the researcher to eyeball",
  "how_it_meets_the_protocol": "markdown: metric by metric, where each number comes from and why it is the quantity the protocol defines",
  "seeded": "markdown: every source of randomness and how $HIRAKU_SEED reaches it",
  "unverified": "markdown: what you could not check locally, and what would check it — or null",
  "blocked": "what stopped you, naming the record line or missing input — or null if you finished",
  "notes": "anything else the researcher should know, or null"
}
```
