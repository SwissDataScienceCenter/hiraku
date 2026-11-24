# MLflow access matrix

Run from the repository with uv installed:

```bash
uv run python scripts/mlflow_access_matrix.py \
  --versions 2.17.2 2.22.0 3.0.0 3.10.0 3.16.0 \
  --output /tmp/hiraku-mlflow-matrix
```

The output directory must not exist on the first run. The script downloads
isolated Python 3.11 environments: the user's server uses `mlflow`, and Hiraku's
client uses `mlflow-skinny` and the checkout without its MLflow extra.
Alternate client versions are experimental compatibility probes, not supported
dependency configurations. Set `--python`
to select another Python version.

The default five versions produce 75 cases. `--jobs` controls concurrency
(default: three). To continue an interrupted run or add versions, repeat the
command with `--resume` and the same output directory. Completed cases and
environments are reused; incomplete cases are recreated. Stop any orphaned
servers from an interrupted run before resuming. Resume assumes the checkout,
Python selection, and environment constraints have not changed.

Server environments constrain SQLAlchemy to `<2.1`: MLflow 2.22.0 imports a pool
class removed in SQLAlchemy 2.1. This keeps the probe focused on MLflow version
compatibility instead of that unrelated dependency failure.

For every user/client version pair, the script tests:

- A missing SQLite database, an existing empty file, and a database containing
  a tagged run created with the user version.
- HTTP access using Hiraku's client version and a server running the user version.

Each case gets a fresh working directory and database copy. Only disposable
databases in the output directory are opened. MLflow environment variables are
removed from child process environments. Servers bind to loopback and are stopped
after each case. This script currently requires POSIX process-group support.

`report.md` summarizes query outcomes and storage changes. `results.json` records
schema definitions, Alembic revisions, row fingerprints, and file fingerprints.
For servers, startup and query changes are recorded separately. Per-case server
and query logs preserve errors. Environment setup and seeding failures stop the
script; individual query failures are expected matrix outcomes.

The final report also inventories subdirectories left in each case, including
empty directories. Cases start without subdirectories; this final inventory does
not distinguish directory creation during server startup from creation during a
query. Row fingerprints include migration metadata and newly added tables, so a
changed fingerprint alone does not imply loss or corruption of existing runs.

Schema changes on empty/missing databases mean initialization, not an upgrade of
existing user data. A changed Alembic revision on a populated database indicates
a migration. File changes without logical schema/data changes can reflect SQLite
journaling or other filesystem side effects. Fingerprints cover the case working
directory, not arbitrary external locations, and this is not a proof that all
versions or deployment configurations are safe. Server probes use Hiraku's SDK
calls, which can include POST-based search endpoints; this is not a GET-only test.

Results and environments are intentionally kept outside the repository. Removing
the output directory after the script finishes removes those disposable artifacts.
