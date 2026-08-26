# Hiraku smoke example

Copy this directory outside the Hiraku checkout, then run:

```bash
git init -b main
hiraku init
git add .
git commit -m "initialize smoke example"

hiraku task create smoke --template smoke
git switch hiraku/task/smoke
git add .
git commit -m "create smoke task"

hiraku experiment create smoke:baseline \
  --summary "Run the deterministic baseline" \
  --entrypoint "train --score 0.50"
hiraku experiment set smoke:baseline --commit HEAD
hiraku launch --experiment baseline smoke:train --score 0.50
hiraku runs smoke --sort score --descending
hiraku experiment set smoke:baseline \
  --status evaluated \
  --decision accepted \
  --conclusion "The deterministic baseline completed."
```

The launcher executes the committed source from a detached temporary worktree while the script writes its run to `tasks/smoke/runs.csv` in the original checkout.
