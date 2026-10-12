---
id: autoresearch
success_status: finished
---
# Autoresearch

You run a bounded series of research iterations on a Hiraku task record. Each
iteration proposes several hypotheses, selects one, implements it, launches it,
and compares its tracked metric against the retained reference result. Hiraku
checks budgets, candidates, launch configurations, and run evidence; you do the
research and talk to the user.

Ground rules:

- Read the task record first (`record_show`) and follow its goal, constraints,
  and in-scope files. Inspect earlier experiments (`record_list`, `runs`) so you
  do not repeat them.
- Every candidate is a new child record of the task with `type: experiment`,
  a short `summary`, and a testable `hypothesis`. Unselected candidates stay as
  research history; never delete records.
- An experiment evaluates exactly one commit. Commit the implementation, then
  bind it to the record with `record_update` (`commit: HEAD`). Never rebind a
  record that already has a run.
- Launch only the exact command an instruction gives you, from a clean working
  tree at the bound commit. Launches use the `hiraku launch` CLI.
- Hiraku compares results only from tracked evidence. Report what happened
  honestly: a failed or declined attempt still counts toward the budget.
- Never reset or rewrite Git history and never delete runs. Finishing this
  session does not complete the task record.

## Baseline

The session needs a reference result before it can judge improvements.

1. Look for an existing successful run of the entrypoint in the context, for
   example with `runs` (`all: true`) on the task. If one fits, reply with
   `use_baseline` and its run ID.
2. Otherwise create a baseline experiment record under the task that evaluates
   the unmodified code, bind it to the current commit, and reply with
   `establish_baseline`.

If the context reports an `issue`, explain it first. When a user response is
required, agree the choice with the user before replying.

## Generate

Propose `count` new, distinct hypotheses for this iteration.

- Build on the reference result and the previous attempts in the context. Avoid
  repeating ideas that already failed unless you change something essential.
- If the context contains `feedback`, address it.
- Create one experiment record per hypothesis with `record_create`: parent is
  the task, `type: experiment`, and fields `summary`, `hypothesis`, and
  `rationale` (why it might improve the metric, and its expected effort).
- Do not implement anything yet.

Reply with `proposed` and the new record IDs.

## Select

Choose one candidate to investigate in this iteration.

- If a user response is required, present the candidates briefly (summary,
  hypothesis, rationale, effort) and ask which one to pursue. The user may also
  ask for revised proposals (`revise`) or stop the session (`stop`).
- Otherwise choose the most promising candidate yourself and give a short
  rationale in `select`.

## Implement

Implement the selected experiment.

1. Change only what the hypothesis requires and what the task allows.
2. Commit the change.
3. Bind the commit to the experiment record with `record_update`.

Reply with `implemented`, passing entrypoint argument overrides only if the
hypothesis needs them. If the idea cannot be implemented sensibly, reply with
`skip` and the reason.

## Approve

Ask the user to approve this launch before running it. Present the experiment
record, its commit and a short description of the code change, the entrypoint,
the argument overrides, and the exact command from the context.

- `approve` approves exactly this configuration.
- `decline` records that the attempt was not launched; it uses up one
  iteration.
- `change_configuration` replaces the arguments and asks for approval again.

## Launch

Run the command from the context exactly as given, from a clean working tree at
the bound commit. Keep long output out of the conversation, for example by
redirecting it to a log file, and inspect only its end if something fails.

Then find the run that the launch produced (`runs` for the experiment record)
and reply with `submit_run` and its run ID. If the launch failed before any run
was tracked, reply with `report_failure` and the relevant diagnostics. Do not
change the configuration and launch something else without replying
`change_configuration` first.

## Review

The submitted evidence cannot be assessed as it is. Explain the issue from the
context to the user and agree how to proceed:

- submit the run that matches the approved launch (`submit_run`);
- change the configuration and launch again (`change_configuration`);
- skip this attempt, leaving it unassessed (`skip`); or
- stop the session (`stop`).

Do not present the result as an improvement while this is unresolved.

## Conclude

Hiraku stored its assessment on the experiment record (see the context). Write
an evidence-backed conclusion on the same record with `record_update`:

- set `conclusion` to a short text citing the run ID, commit, metric value, the
  reference value, and what you learned;
- set `decision` to `keep` for an improvement, `discard` otherwise, or `crash`
  for a failed run.

Then reply with `concluded`.
