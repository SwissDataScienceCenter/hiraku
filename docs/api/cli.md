# CLI Reference

The CLI manages research records, launches, and tracked runs.

Typical workflow:

```bash
hiraku init
hiraku record create <root-id>
hiraku record create <child-id> --parent <root-id>
hiraku launch <entrypoint> --record <child-id>
hiraku runs --record <record-id>
```

`hiraku record list` includes each record's local `status` in its table.

Install Hiraku with the optional dashboard extra (from a Hiraku checkout),
then serve records from the current project:

```bash
uv tool install --force '.[dashboard]'
hiraku dashboard
```

The dashboard runs in Hiraku's environment, not the project's, so it works in
projects with their own virtual environment. Add libraries for custom plots to
that environment, e.g. `uv tool install --force '.[dashboard]' --with seaborn`.

Marimo automatically chooses a free port starting at 2718.
Use `--port 2719` to request a fixed port. The dashboard is read-only;
reload it to read changed records and runs.

To customize the dashboard, run `hiraku dashboard --init`. This copies the
bundled notebook and view into `hiraku/dashboard` (or the configured
`hiraku_dir`). Edit its `app.py` and view files; subsequent `hiraku dashboard`
commands automatically serve the project copy. Initialization refuses to
overwrite an existing dashboard and requires no dashboard dependencies.

Run `hiraku dashboard --edit` to open the project copy in the marimo editor,
or `hiraku dashboard --init --edit` to create and open it in one step. The
editor reloads changes saved from other tools. Prefer these commands over
calling `marimo` directly: they start marimo with the Python environment
that has Hiraku and the dashboard dependencies installed.

Each cell in `app.py` delegates loading and rendering to
`hiraku.dashboard.components`, so the copy stays short and picks up fixes from
Hiraku upgrades. To add a plot, add a cell that reads variables such as `runs`,
`record`, or `selected_id`, then place it in `views/dashboard/index.html`:

```python
@app.cell
def loss_plot(runs, select_record, selected_id):
    loss = hd.run_frame(runs, "val/log_loss", selected_id)
    loss_chart = hd.run_chart(loss, "val/log_loss", select_record)
    loss_chart
    return (loss_chart,)
```

```html
<marimo-cell name="loss_plot"></marimo-cell>
```

To replace the bundled chart, edit the `run_plot` cell instead.

`hiraku init` requires its target to be a Git repository root. Use
`hiraku init PATH --ignore-git` to intentionally initialize a nested or non-Git
location. Python initialization is permissive by default; callers can request
the CLI policy with `ignore_git=False`.

Initialize from an arbitrary local project overlay with `--template`:

```bash
hiraku init --template ./path/to/template
```

Packaged examples use `--example NAME`. For example, start with the minimal
local evaluation project using:

```bash
hiraku init --example minimal
```

Each example copies only its bundled
`src/` subtree by default. Add `--results` to apply its optional `results/`
overlay with sample records and synthetic runs:

```bash
hiraku init --example heart-disease --results
```

Results may override base files and configuration keys, but neither layer
overwrites pre-existing project files. Explicit CLI options take precedence.
The flag requires a packaged example with a `results/` directory.
Sibling documentation and metadata are not copied:

```text
resources/examples/<name>/
├── README.md
├── example.yml
├── src/                 # copied into the project root
└── results/             # optional overlay with --results
```

The two options are mutually exclusive. Templates may replace files installed
by the same initialization, but initialization refuses to overwrite files that
already existed in the target. Explicit options such as `--tracking-backend`
take precedence over values from a template-provided `hiraku.yml`.
After successful initialization, the command lists every file and directory it
created before printing the final project location.

Use `hiraku --help` or the generated command sections below for all options.
The command reference is generated from Hiraku's Click command tree.

::: mkdocs-click
    :module: hiraku.cli
    :command: main
    :prog_name: hiraku
    :depth: 1
    :style: table
    :list_subcommands: true
