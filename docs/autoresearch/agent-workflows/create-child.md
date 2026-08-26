# Create a child Experiment

Confirm the parent has an evaluated commit and obtain approval. Then run:

```bash
hiraku experiment create <goal>/<child> \
  --parent <goal>/<parent> \
  --hypothesis "..."
```

The command creates and checks out `hiraku/<goal>/<child>` from the parent's frozen commit. Do not
derive lineage from Git branch names.

