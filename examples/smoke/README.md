# Deterministic smoke experiment

This dependency-free example verifies the Hiraku lifecycle without testing a model. Its two fixed
entrypoints append deterministic scores to the configured run table.

From a clean Git repository containing this `examples/smoke` directory and an installed `hiraku`:

```bash
hiraku init
git add . && git commit -m "initialize hiraku"
hiraku exp create smoke-demo
cp examples/smoke/launch.yml experiments/smoke-demo/launch.yml
git add . && git commit -m "configure smoke experiment"
hiraku launch smoke-demo:baseline
hiraku launch smoke-demo:candidate
hiraku runs smoke-demo --sort score --descending
```

The candidate ranks first. The metric is intentionally synthetic; this example tests workflow
plumbing only.

