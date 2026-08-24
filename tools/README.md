# tools/

Standalone scripts that are not part of the `hiraku` package.

## `hiraku_dash.py`

Renders a Hiraku project's experiment tree as one self-contained HTML page.

```bash
python tools/hiraku_dash.py examples/natix-vo -o dashboard.html
```

Standard library only. PyYAML is used for front matter when it is importable, with a fallback
parser covering the subset in [`docs/autoresearch/data-model.md`](../docs/autoresearch/data-model.md); the two
are checked to agree on every record in both bundled projects.

Read-only: it never writes into the project, which is what makes it safe to run from a hook or in
CI. The markdown records and the run table are the source of truth — if the page and the records
disagree, the records are right and this script has a bug.

It also reports record problems on stderr, and shows them in the page: dangling `parent` or `goal`
references, two records claiming one ID, cycles in the parent chain, a `goal` that disagrees with
the parent's, a front-matter `id` that disagrees with its directory, a node concluded without a
verdict, a verdict on a node that is not concluded, an unknown `kind`/`status`/`verdict`, and a node
with no recorded expectation.

Exit status is 0 even when checks fire — it is a viewer, not a linter. Validation belongs in a
`hiraku lineage check` command; see
[`docs/autoresearch/README.md`](../docs/autoresearch/README.md#deferred-to-the-cli).

The script is split so that `load_project()` returns a plain dict and `render()` is a pure function
of it, so `hiraku dash --serve` can reuse the parsing half and re-render on change. See
[`docs/autoresearch/dashboard.md`](../docs/autoresearch/dashboard.md).
