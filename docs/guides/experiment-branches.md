# Branches and record provenance

Hiraku records may name an optional Git branch and an implementation commit.
Each record carries its own values; bind it to the source commit used for its runs.
The knowledge base has user-managed versioning independent of the code checkout.

Use Git directly to create branches, commit changes, and merge. Hiraku stores
record branches as metadata and resolves commit references without managing
branches:

```bash
git switch -c hiraku/records/baseline
hiraku record create baseline --parent research-root \
  --set 'branch=hiraku/records/baseline' --commit HEAD
```

The logical record tree, `related_to` links, and Git commit history capture
different relationships. Use `parent_id` for logical hierarchy,
`related_to` for cross references, and commit ancestry for code provenance.
