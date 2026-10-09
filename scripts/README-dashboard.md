# Test the Hiraku dashboard

Run these commands from the Hiraku repository using Python 3.11+ and uv:

```bash
uv sync --all-extras
uv run --all-extras python scripts/test_dashboard.py
```

The script runs the dashboard regression tests and marimo's notebook check,
creates a fresh temporary Git/Hiraku project from the bundled
[Digits MLflow example](https://swissdatasciencecenter.github.io/hiraku/examples/digits-mlflow/),
and trains the weak-L2 and strong-L2 models for 40 epochs each. It uses the
example's actual `train.py`, the bundled scikit-learn Digits dataset, the same
seed (42), and regularization strengths (0.0001 and 1.0). Dependencies come from
the checkout's uv environment, so no second virtual environment is needed.

Runs are launched through Hiraku's dispatcher with real record, entrypoint,
and Git commit attribution. Training writes to the demo's own `mlflow.db` and
`hiraku-digits` experiment, even if your shell has MLflow overrides set. No
dataset download, tracking server, or credentials are required. Git and uv
must be installed. The script verifies that both real runs and their metrics
reach the dashboard's Progress and Pareto transformations before starting
both the Hiraku dashboard (with `weak-l2` selected) and MLflow UI. Both URLs
are printed and opened in the browser:

- Hiraku dashboard: <http://127.0.0.1:8765>
- MLflow UI: <http://127.0.0.1:5000>

Hiraku starts in **All attributed runs in this source**, so both experiments
are visible immediately. Progress defaults to `val/log_loss`; Pareto defaults
to `val/log_loss` (minimize) against `val/accuracy` (maximize). The printed MLflow
link opens this experiment's **Model training → Runs** view, rather than the
GenAI overview used by MLflow 3's default homepage.

Press **Ctrl-C** to stop both servers. If either server fails to start or exits,
the script stops the other as well. The CSV demo starts only the Hiraku dashboard.

The printed **Demo project** path is retained after shutdown. Existing
projects are never overwritten. The normal regression run skips browser
automation unless explicitly enabled below; follow the manual checks to
exercise the UI yourself.

## Digits walkthrough

1. **Record details:** the initial `weak-l2` record inherits its metric directions
   and tracking backend from `digits`. Its training entrypoint uses alpha
   0.0001, 40 epochs, 64 hidden units, and seed 42.
2. **Runs:** the initial source scope shows both `weak-l2` and `strong-l2`.
   Switch to selected-record scope to see only the weak-L2 run.
   Selecting `digits` in selected-record scope shows no runs: the runs belong
   to its child records. Use source scope to see both.
3. **Provenance:** select either run. Its record ID, Git commit, and `train`
   entrypoint should be populated. The Open record button navigates to the
   attributed experiment. Sort by a validation metric to compare results.
4. **Progress:** the default is `val/log_loss`. In source scope there should be two
   points, one per training run, with a minimize best-so-far overlay. Click a
   point and inspect **Selected plot points**.
5. **Pareto:** switching views should immediately show `val/log_loss` on X
   (minimize) and `val/accuracy` on Y (maximize). Both runs should appear, with
   nondominated runs identified in the legend.
6. **Refresh:** run the command below in another terminal, using the printed
   demo path. It retrains the weak-L2 model through Hiraku and records an
   additional MLflow run. Press **Refresh**; source scope should then show three
   runs and three plot points (identical deterministic scores may overlap).

```bash
uv run --all-extras python scripts/test_dashboard.py --launch-run "<demo-path>"
# Or repeat the strong-L2 experiment:
uv run --all-extras python scripts/test_dashboard.py --launch-run "<demo-path>" --record strong-l2
```

Dashboard plots compare the **latest summary metric per run**, not individual
training epochs. Full 40-epoch learning curves are recorded in MLflow. Open
the printed MLflow UI URL, select `hiraku-digits`, then compare the two runs
and their `val/log_loss`, `val/accuracy`, `train/log_loss`, and `train/accuracy`
histories.

For an easy preview, open either run's **Artifacts** tab, then
`plots/learning-curves.png`. Each new run also logs `metrics/epoch-metrics.csv`
and `results/final-metrics.json`. Older demos created before this change keep
their original copy of `train.py` and do not have these artifacts; create a
fresh demo to use the updated logging.

To reopen only MLflow UI after stopping the script, run from the repository:

```bash
uv run --all-extras mlflow ui --backend-store-uri "sqlite:///<demo-path>/mlflow.db" --host 127.0.0.1 --port 5000
```

## CSV edge-case walkthrough

The original synthetic fixture remains available for missing metrics, empty
sources, and repeated IDs:

```bash
uv run --all-extras python scripts/test_dashboard.py --example csv
```

1. **Record details:** select `child-a`. Its metric directions come from
   `baseline`; its tracking source is `results/a.csv`. Inspect the defining
   record IDs in the fields table.
2. **Scope:** the selected-record scope has two runs, `a-1` and
   `a-missing`. Switch to **All attributed runs in this source** to see three
   runs, including `shared-b`, attributed to `child-b`.
3. **Sort and provenance:** sort by `metric.acc`, toggle Ascending, and
   select a run. Inspect its record ID and source. These fixtures have no
   recorded commits, so the detail panel should say metadata unavailable.
   Select `shared-b` and use **Open record child-b** to navigate.
4. **Repeated IDs:** select `child-b`. Its `a-1` run has accuracy 0.9 and
   loss 0.2, versus 0.8 and 0.3 for `child-a`. The sources must stay distinct.
5. **Progress:** return to `child-a`, selected-record scope. Click the plotted
   point and inspect **Selected plot points**. One run is omitted because its
   metrics are missing.
6. **Pareto:** switch to Pareto with `acc` on X and `loss` on Y. The inherited
   directions should be maximize and minimize respectively.
7. **Empty data:** select `empty`. It should show no runs without creating
   the missing CSV file.
8. **Refresh:** return to `child-a`. In a second terminal, from the repository,
   run the command below using the printed demo path. The new run should
   appear after pressing **Refresh**. Periodic refresh starts off.

```bash
uv run --all-extras python scripts/test_dashboard.py --append-run "<demo-path>"
```

Each invocation adds a uniquely named `refreshed-N` run with accuracy 0.95
and loss 0.1. This command intentionally modifies only the demo's CSV file.
It refuses directories without the script's demo marker.

Stop the server with **Ctrl-C**. You can inspect or delete the temporary demo
directory afterward.

## Useful options

```bash
# Choose other ports and open the printed URLs yourself.
uv run --all-extras python scripts/test_dashboard.py --port 8766 --mlflow-port 5001 --no-browser

# Skip tests that have already passed.
uv run --all-extras python scripts/test_dashboard.py --skip-tests

# Run checks and create data without starting either server.
uv run --all-extras python scripts/test_dashboard.py --no-serve

# Train a shorter Digits demo (results differ from the 40-epoch example).
uv run --all-extras python scripts/test_dashboard.py --epochs 8 --skip-tests

# Choose a new or empty directory.
uv run --all-extras python scripts/test_dashboard.py --project-dir "<new-demo-path>"
```

To reopen an existing demo, change into its directory and use the repository
path with uv. Do not rerun the creation script against a populated directory.

```bash
uv run --project "<hiraku-repository-path>" --all-extras hiraku dashboard --record weak-l2
```

For a CSV demo, use `--record child-a` instead.

## Automated browser walkthrough

Selenium is installed with the development dependencies. With Chrome and its
driver available:

```bash
uv run --all-extras python scripts/test_dashboard.py --browser-tests chrome --no-serve
```

On Linux, install WebKitGTK's driver (`webkitgtk-webdriver`, or
`webkit2gtk-driver` on older distributions) and `xvfb`, then run:

```bash
xvfb-run -a uv run --all-extras python scripts/test_dashboard.py --browser-tests webkit --no-serve
```

The automated browser test uses its own temporary fixture and verifies
record/run selection, provenance navigation, sorting, point selection,
Pareto switching, refresh, shutdown, and unchanged research files.

If port 8765 or 5000 is occupied, choose another with `--port` or
`--mlflow-port`. The two ports must differ. `--no-browser` suppresses both
browser tabs. If a check fails,
the script stops before launching the demo. See the test output for the
failure; `--skip-tests` is intended for checks you have already run.
