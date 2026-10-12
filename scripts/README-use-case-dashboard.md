# Interactive dashboard demos

Run any documented Hiraku use case in an isolated project and open its
interactive dashboard. The helper creates records and initial runs where the
example supports local tracking, starts the GUI, and prints the access token.
Run the commands below from the Hiraku source checkout.

## Requirements

- Python 3.11 or newer, Git, and [uv](https://docs.astral.sh/uv/).
- Install the development environment with `uv sync --all-extras`.
- Ports 8765 (Hiraku) and, for Digits, 5000 (MLflow) must be free. Choose
  different ports with the options below if they are occupied.

## Start a demo

Choose one command. Each invocation creates a new, retained demo directory:

```bash
uv run --all-extras python scripts/use_case_dashboard.py --example minimal
uv run --all-extras python scripts/use_case_dashboard.py --example heart-disease
uv run --all-extras python scripts/use_case_dashboard.py --example digits-mlflow
uv run --all-extras python scripts/use_case_dashboard.py --example iris-wandb
uv run --all-extras python scripts/use_case_dashboard.py --example autoresearch
```

When startup finishes, the terminal prints **Hiraku GUI is now running on:**,
**Tracking backend on:**, **Hiraku token is:**, and **Demo project:**. Open the
complete GUI URL, including its `access_token` query parameter. The helper
also opens the browser unless you use `--no-browser`. Keep the token private:
it grants access to record creation and entrypoint launches in this local
dashboard session. The tracking location is a CSV path, the MLflow UI URL, or
the W&B project location, depending on the example.

## Reopen a demo

Save the printed **Demo project** path. After stopping the servers with
**Ctrl-C**, start them again without recreating records or repeating the
initial runs:

```bash
uv run --all-extras python scripts/use_case_dashboard.py --reopen "<demo-path>"
```

Reopening starts a new GUI session and prints a new token. For Digits it also
starts the local MLflow UI. The demo files stay in the printed directory after
the servers stop.

## Options

```bash
# Avoid an occupied GUI port; Digits can also move its MLflow UI.
uv run --all-extras python scripts/use_case_dashboard.py --example digits-mlflow --port 8766 --mlflow-port 5001

# Create a demo at a chosen new or empty path, without opening browser tabs.
uv run --all-extras python scripts/use_case_dashboard.py --example minimal --project-dir "<new-demo-path>" --no-browser

# Prepare records and initial runs without serving the GUI.
uv run --all-extras python scripts/use_case_dashboard.py --example heart-disease --no-serve
```

`--port`, `--mlflow-port`, and `--no-browser` also work with `--reopen`.
`--epochs N` changes the Digits training budget (default: 8). `--no-serve`
prints only the demo path; it does not print a GUI URL or token.

## Example notes

| Example | Initial result | Tracking location |
| --- | --- | --- |
| `minimal` | One baseline evaluation | Local `runs.csv` |
| `heart-disease` | Logistic and random-forest runs | Local `runs.csv` |
| `digits-mlflow` | Two short training runs | Local MLflow database and UI |
| `iris-wandb` | Records ready; no remote runs by default | W&B `hiraku-iris` project |
| `autoresearch` | CPU mock run on supported Linux hosts | Local `runs.csv` |

To log Iris runs, first authenticate with W&B, then run:

```bash
uv run --all-extras python scripts/use_case_dashboard.py --example iris-wandb --wandb-entity <account-or-team> --run-iris
```

`WANDB_ENTITY` can supply the entity instead. The helper passes the chosen
entity and project to later GUI launches. Without `--run-iris`, it does not
contact W&B; run lists may be empty. The Autoresearch mock requires Linux with
`timeout` and `flock`; other systems get prepared records only. Full
Autoresearch training requires the upstream checkout and a suitable GPU.

## More help

The [interactive dashboard manual](../docs/guides/dashboard-manual.md) walks
through record creation, launches, jobs, and results. The
[dashboard reference](../docs/dashboard.md) explains scopes, plots, security,
and limitations. For the older detailed Digits and CSV test walkthrough, see
[README-dashboard.md](README-dashboard.md).
