# Digits Snakemake example

The project files copied by `hiraku init --example digits-snakemake` are stored
in `src/`. See `docs/examples/digits-snakemake/README.md` in the Hiraku
repository for the complete setup and usage guide.

The workflow materializes scikit-learn's bundled Digits split as `data/digits.npz`
and trains weak (`alpha=0.0001`) and strong (`alpha=1.0`) L2 variants with the
same 64 hidden units, seed, split, and 40-epoch budget. Each training step
writes `results/<variant>.json` atomically and logs epoch histories to a local
`mlflow.db`.

Each rule launches an entrypoint of a record: `digits` · `prepare`,
`weak-l2` · `train`, and `strong-l2` · `train`. The records state the code and
arguments; the `Snakefile` states the files. The workflow record keeps its
execution runs in `hiraku/runs.csv`, next to the records. The experiment
records use MLflow and tag their runs with `HIRAKU_WORKFLOW_RUN_ID`. The
entrypoints run `python`, so run single steps inside the project environment
with `uv run hiraku launch ...`.

The `Snakefile` is optional: without it, `hiraku snakemake run` builds the same
rules from steps launched once with `uv run hiraku launch ... --observe`.
Keeping it suits agentic work, since an agent can write and review the rules as
a diff and check them with `--dry-run` before any training.
