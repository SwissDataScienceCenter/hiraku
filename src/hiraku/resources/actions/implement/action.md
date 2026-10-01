---
id: implement
accepted_types: [experiment]
effect: transition
from_status: draft
to_status: implemented
hooks: hooks.py
---
# Implement an experiment

Implement the selected experiment using its summary, hypothesis, expectation,
Markdown brief, and entrypoints. Inspect the existing code before changing it.
Additional user instructions refine this brief; no message is required.
Preserve existing models and metrics unless the brief requires changing them.
Run focused checks for the implementation and summarize the changes and results.

Only write implementation code and associated tests or documentation. Do not
edit Hiraku records or configuration, or create Git commits. Hiraku checks the
result, commits the implementation, binds the commit hash to the record, and
advances its status deterministically.

## Current state

```json
{{state}}
```

## Additional instructions

{{instruction}}
