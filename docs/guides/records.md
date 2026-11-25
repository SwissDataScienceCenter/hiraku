# Records

Hiraku stores research records in markdown knowledge base following the [Open Knowledge Format](https://github.com/open-knowledge-format/open-knowledge-format).

Markdown records are organized in a user-defined directory tree. A record ID is the path relative to the records directory (`hiraku/records/` by default), without `.md`. For example, `classification/baseline.md` has ID `classification/baseline`. Use `--parent` when creating a child record to declare the relationship. A record can also store the Git commit for its implementation.

## Commits and Branches

Hiraku records optionallly refers to Git branch and a local code
commit

Use Git directly to create branches, commit changes, and merge. Hiraku stores
only metadata without managing branches:

```bash
git switch -c hiraku/records/baseline
hiraku record create baseline --parent research-root --type experiment \
  --set 'branch=hiraku/records/baseline' --commit HEAD
```

The logical record tree, `related_to` links, and Git commit history capture
different relationships. Use `parent_id` to declare research relationships,
`related_to` for cross references, and commit ancestry for code provenance.
