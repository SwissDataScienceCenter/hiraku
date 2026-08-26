# Run a bounded loop

Approve Goal scope, allowed entrypoints, implementation command, experiment/run limits, time budget,
and policy first. Then:

```bash
hiraku auto loop <goal> --max-experiments 5 --max-runs 20 --time-budget 2h
```

The loop uses the shared core serially. It records baseline and candidate Runs, commits approved
implementation-command changes, analyzes the Goal metric, and concludes each Experiment. It stops
on implementation, execution, or research-result failures and records the reason as an event.

