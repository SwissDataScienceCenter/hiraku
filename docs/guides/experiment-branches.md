# Branches and record provenance

Hiraku records may name an optional Git branch and a local implementation
commit. The `branch` field inherits through `parent_id`, so a child may share
its parent's working branch or override it. `commit` never inherits: bind each
record to the exact source commit used for its own runs.

The Git repository remains responsible for creating branches, committing
changes, and merging. Hiraku's `record_branch` configuration provides a
default branch name for code using `GitRepository.create_record_branch`; the
CLI also accepts a branch as a stored field:

```bash
git switch -c hiraku/records/baseline
hiraku record create baseline --parent research-root \
  --set 'branch=hiraku/records/baseline' --commit HEAD
```

The logical record tree, `related_to` links, and Git commit history capture
different relationships. Use `parent_id` for hierarchy and inheritance,
`related_to` for cross references, and commit ancestry for code provenance.
