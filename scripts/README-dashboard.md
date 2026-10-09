# Test the Hiraku dashboard

Run these commands from the Hiraku repository using Python 3.11+ and uv:

```bash
uv sync --all-extras
uv run --all-extras python scripts/test_dashboard.py
```

The script runs the dashboard regression tests and marimo's notebook check,
creates a fresh temporary research project, and launches the dashboard with
`child-a` selected. The browser opens automatically. The test data uses local
CSV files and requires no tracking credentials.

The printed **Demo project** path is retained after shutdown. Existing
projects are never overwritten. The normal regression run skips browser
automation unless explicitly enabled below; follow the manual checks to
exercise the UI yourself.

## Manual walkthrough

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
# Choose another port and open the printed URL yourself.
uv run --all-extras python scripts/test_dashboard.py --port 8766 --no-browser

# Skip tests that have already passed.
uv run --all-extras python scripts/test_dashboard.py --skip-tests

# Run checks and create data without starting a server.
uv run --all-extras python scripts/test_dashboard.py --no-serve

# Choose a new or empty directory.
uv run --all-extras python scripts/test_dashboard.py --project-dir "<new-demo-path>"
```

To reopen an existing demo, change into its directory and use the repository
path with uv. Do not rerun the creation script against a populated directory.

```bash
uv run --project "<hiraku-repository-path>" --all-extras hiraku dashboard --record child-a
```

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

If port 8765 is occupied, choose another with `--port`. If a check fails,
the script stops before launching the demo. See the test output for the
failure; `--skip-tests` is intended for checks you have already run.
