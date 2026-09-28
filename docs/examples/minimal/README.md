# Start from scratch

Initialize a Git project, then install the small CSV example:

```bash
mkdir my-project && cd my-project
git init
hiraku init --example minimal
git add . && git commit -m "initial commit"
```

Create a root research record and one focused child. The child inherits
metrics and any entrypoints defined on its parent. Here its entrypoint is
specific to the baseline:

```bash
hiraku record create my-task --type task \
  --set 'goal=Improve validation accuracy' \
  --set 'metrics=[{name: val/accuracy, direction: maximize}]'

hiraku record create baseline --parent my-task --type experiment --commit HEAD \
  --set 'entrypoints={evaluate: {command: [uv, run, python, evaluate.py], args: {seed: 0}}}'

hiraku launch evaluate --record baseline
hiraku launch evaluate --record baseline -- --seed 1
hiraku runs --record my-task --descendants --json
```

`evaluate.py` logs `record_id`, source commit, Git state, arguments, and
validation accuracy in project-root `runs.csv`. Replace it with your own
evaluation while keeping that attribution. Add another child record for the
next implementation or diagnosis.
