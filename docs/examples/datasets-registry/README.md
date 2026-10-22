# Datasets registry

Register existing datasets and their artifact descriptors as ordinary Hiraku
records. Registration observes data and documents its source; it never runs
workflows, fabricates runs, copies dataset contents, or computes metrics.

This example starts from a clean install and walks through preview → register →
browse → unchanged reimport → changed revision → partial refresh, ending with a
descriptor-only remote dataset.

## Initialize

```bash
mkdir datasets-registry
cd datasets-registry
git init
hiraku init --example datasets-registry
```

The example ships `data/measurements.csv` (a local file) and
`remote-registry.yaml` (a descriptor-only manifest whose artifact has only a
remote access URI).

## Register local files

Discovery lists files without reading their contents; each revision is the
observed `size:mtime` pair.

```bash
hiraku registry scan --root data            # preview, writes nothing
hiraku registry scan --root data --json
hiraku registry scan --root data --apply    # write the dataset record
```

`hiraku registry scan --output registry.yaml` writes the reviewed manifest
(including its `content_sha256`) instead of importing it.

## Register a descriptor-only remote dataset

```bash
hiraku registry import remote-registry.yaml            # preview
hiraku registry import remote-registry.yaml --apply
```

Descriptor-only registration never opens the artifact location, so the remote
URI can point anywhere.

## Browse

```bash
hiraku dashboard
```

The **Datasets** section lists every registered artifact with its source,
revision, observation time, coverage, availability, and documentation links,
independent of workflow runs.

## Idempotence and refresh

Re-importing the same manifest makes no edits and leaves `git status` clean:

```bash
hiraku registry import remote-registry.yaml --apply --json   # unchanged
```

Touching a local file changes its `size:mtime` revision, and an exhaustive
rescan records the new revision and demotes files that disappeared to `stale`:

```bash
touch data/measurements.csv
hiraku registry scan --root data --apply
```

A partial scan merges evidence without claiming absence:

```bash
hiraku registry scan --root data --apply    # exhaustive over data/
```

## Records

Each dataset is a normal Hiraku record of `type: dataset`:

```bash
hiraku record list --json
hiraku record show <dataset-id> --json
```

Its `registration` front matter is readable by main's Hiraku without any
registry code.
