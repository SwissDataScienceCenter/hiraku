# Record data format

Each root record owns one directory. All descendants of that root are flat
files in the same directory:

```text
hiraku/records/heart-disease/heart-disease.md
hiraku/records/heart-disease/logistic-baseline.md
hiraku/records/heart-disease/calibration-diagnosis.md
```

The `parent_id` field, rather than the file path, defines the logical tree.
Use `hiraku record update ID --parent NEW_PARENT` or `Record.reparent()` to
move a record and its descendants across roots; `--root` makes it a root.
Use `hiraku record tree` to inspect nesting. Directly editing `parent_id`
across roots also requires moving the files to the matching directory.

```markdown
---
id: logistic-baseline
parent_id: heart-disease
type: experiment
related_to: [prior-feature-selection]
commit: 0123456789abcdef0123456789abcdef01234567
entrypoints:
  train:
    command: [uv, run, python, train.py]
    args:
      model: logistic
status: implemented
---
# Logistic baseline

Evaluate validation log loss against the shared task metrics.
```

Any other YAML field may document the research. Omitted fields inherit through
`parent_id`; an explicit `null` blocks inheritance. Entrypoints merge by name.
`id`, `parent_id`, `related_to`, `type`, `commit`, `status`, and `decision` do not
inherit. `related_to` has no effect on the tree.

The generic `BaseRecord` class provides Markdown persistence to research
records, agent programs, actions, and contexts. Only research `Record` objects
use the `hiraku/records/` registry and inheritance rules.
