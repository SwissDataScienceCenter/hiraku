# Smoke example

Run from the Hiraku repository root. Commit the implementation once before creating the Goal so
its immutable baseline contains the example code.

```bash
uv sync --extra dev --extra tui
git add .
git commit -m "install unified Hiraku core"
hiraku init
git add .hiraku.yml .hiraku/.gitignore .agents
git commit -m "initialize hiraku"
hiraku goal create smoke --metric score --direction max --launch examples/smoke/launch.yml
hiraku experiment create smoke/baseline --kind baseline --hypothesis "The reference establishes the baseline score."
hiraku launch smoke/baseline:evaluate
hiraku launch smoke/baseline:evaluate
hiraku experiment create smoke/better --hypothesis "The alternative improves score."
hiraku launch smoke/better:evaluate
hiraku launch smoke/better:fail
hiraku runs smoke --sort score --descending
hiraku experiment conclude smoke/better --verdict supports
hiraku experiment tree smoke
hiraku report generate smoke
hiraku tui
```

To demonstrate a child Experiment, run:

```bash
hiraku experiment create smoke/better-v2 --parent smoke/better --hypothesis "A refinement preserves the improvement."
hiraku launch smoke/better-v2:evaluate
```

To exercise the unattended runtime-neutral protocol, create a fresh `smoke` Goal and run:

```bash
hiraku auto loop smoke \
  --adapter examples/smoke/protocol_adapter.py \
  --max-experiments 2 \
  --max-runs 2 \
  --time-budget 5m
```

The reference adapter is deterministic and intentionally contains no agent SDK or vendor command.
It demonstrates the transport only: a real integration places its own agent behind the same JSON
stdin/stdout contract.
