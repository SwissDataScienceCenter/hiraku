# Branches and record provenance

Hiraku records may name an optional Git branch and a local implementation
commit. Both `branch` and `commit` are local to each record. Set a child's
`branch` explicitly if it uses the same working branch as its parent, and connect
each record to the exact source commit used for its own runs.

Use Git directly to create branches, commit changes, and merge. Hiraku stores
record branches as metadata and resolves commit references without managing
branches:

```bash
git switch -c hiraku/records/baseline
hiraku record create baseline --parent research-root --type experiment \
  --set 'branch=hiraku/records/baseline' --commit HEAD
```

The logical record tree, `related_to` links, and Git commit history capture
different relationships. Use `parent_id` to declare research relationships,
`related_to` for cross references, and commit ancestry for code provenance.
