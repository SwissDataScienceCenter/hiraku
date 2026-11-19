# Run your first experiment

Evaluate a baseline twice with different seeds and look up both results under one experiment record. You’ll need [Hiraku installed](../../installation.md) and Git configured with your name and email.

## 1. Create a project

```bash
hiraku example create minimal my-project --git
cd my-project
git add .
git commit -m "initial commit"
```

The example creates `hiraku.yml`, a standalone `evaluate.py` script, and a default leaderboard that reads `runs.csv`. The Git commit gives the experiment a code version to reference.

## 2. Record the research goal

```bash
hiraku record create binary-classification \
  --title "Binary classification" \
  --type task \
  --set "goal=Improve validation accuracy on my dataset"
```

This creates `hiraku/records/binary-classification.md`. Use it to keep the goal and summarize results across experiments.

## 3. Describe the baseline

```bash
hiraku record create binary-classification/threshold-baseline \
  --title "Threshold baseline" \
  --parent binary-classification --type experiment \
  --set "hypothesis=The existing representation establishes a reference." \
  --commit HEAD

hiraku record list
```

The experiment has its own record. `--parent` connects it to the task, and `--commit HEAD` records the implementation commit.

## 4. Run the evaluation twice

```bash
hiraku run --record binary-classification/threshold-baseline -- uv run python evaluate.py --seed 0
hiraku run --record binary-classification/threshold-baseline -- uv run python evaluate.py --seed 1
```

Everything after `--` is the command to execute. Hiraku passes the record and job IDs through environment variables. The evaluation script calculates a score and appends it to `runs.csv` along with those IDs and the seed.

## 5. Review the runs and record a conclusion

```bash
hiraku leaderboard runs --record binary-classification/threshold-baseline
hiraku record edit binary-classification/threshold-baseline
```

The leaderboard should show two runs with `param:seed` values of `0` and `1` and a `val/accuracy` score for each. Compare the scores and use the record’s Markdown body to note what you observed.

To use your own evaluation, replace the script and follow the [CSV logging guide](../../guides/tracking-csv.md). Commit the new code and create a new experiment with `--commit HEAD`.
