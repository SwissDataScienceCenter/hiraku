# Install Hiraku

Install Hiraku as a development dependency of your research project and run it
with `uv run hiraku`. Hiraku then uses the same environment as your training
code: it reads MLflow databases with the MLflow version that wrote them, and it
does not depend on which `hiraku` executable your shell finds first.

Hiraku is installed from its GitHub repository. You need read access to the
repository with your usual Git credentials.

## New project

```bash
mkdir my-project
cd my-project
git init
uv init --bare
uv add --dev git+https://github.com/SwissDataScienceCenter/hiraku --branch main
uv run hiraku init
```

`uv init --bare` creates only `pyproject.toml`; skip it if your project already
has one. The `dev` dependency group keeps Hiraku out of your project's runtime
dependencies, while `uv run` installs it by default.

`uv.lock` records the exact Hiraku commit. Update to the latest `main` with
`uv lock --upgrade-package hiraku`, or pin a release with `--tag v0.1.1`
instead of `--branch main`.

## Packaged example

`hiraku init --example` copies the example's `pyproject.toml` and refuses to
overwrite an existing one. Initialize the example with a temporary Hiraku
first, then add Hiraku to the example's environment:

```bash
mkdir heart-disease
cd heart-disease
git init
uvx --from git+https://github.com/SwissDataScienceCenter/hiraku@main \
  hiraku init --example heart-disease
uv add --dev git+https://github.com/SwissDataScienceCenter/hiraku --branch main
```

## Run commands

Prefix commands with `uv run`:

```bash
uv run hiraku status
```

Alternatively, activate the environment once per shell and use `hiraku`
directly, as the examples do:

```bash
source .venv/bin/activate
hiraku status
```

Tracking SDKs such as MLflow or W&B belong to your project. Add them with
`uv add mlflow` or `uv add wandb`; Hiraku imports them from the same
environment.

## Alternatives

**Global tool.** `uv tool install git+https://github.com/SwissDataScienceCenter/hiraku`
installs one `hiraku` for all projects. This is sufficient for CSV tracking and
for initializing projects. Avoid it when Hiraku reads data written by a project
SDK: a tool environment with a different MLflow version may reject the
project's database. If you use it anyway, add the tracking SDK to the tool
environment, e.g. `uv tool install --with mlflow git+https://github.com/SwissDataScienceCenter/hiraku`.

**Hiraku development.** Install a local checkout in editable mode:

```bash
uv add --dev --editable /absolute/path/to/hiraku
```

Use an absolute path: the `worktree` dispatcher runs entrypoints from a
temporary checkout, where a relative path would not resolve.
