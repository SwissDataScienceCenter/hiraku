# Datasets registry

Register existing datasets and their artifact descriptors as ordinary Hiraku
records. Registration observes data and documents its source; it never runs
workflows, fabricates runs, copies dataset contents, or computes metrics.

This example starts from a clean install and walks through preview → register →
browse → unchanged reimport → changed revision → partial refresh, with a
descriptor-only remote dataset along the way.

## Initialize

```bash
mkdir datasets-registry
cd datasets-registry
git init
hiraku init --example datasets-registry
git add -A && git commit -m "initialize datasets-registry example"
```

The example ships:

- `data/measurements.csv`, a local file;
- `remote-registry.yaml`, a descriptor-only manifest whose artifact has only a
  remote access URI;
- `partial-refresh.yaml`, a partial manifest that reports one more file under
  `data/`.

## Register local files

Discovery lists files without reading their contents; each revision is the
observed `size:mtime` pair. The preview writes nothing and prints the scan's
`content_sha256`. Apply by passing that hash back, so files that changed after
the preview are never applied unseen:

```bash
hiraku registry scan --root data            # preview, prints content_sha256
hiraku registry scan --root data --apply --reviewed-hash <content_sha256>
```

`hiraku registry scan --root data --output registry.yaml` writes the manifest,
including its `content_sha256`, instead of importing it.

## Register a descriptor-only remote dataset

```bash
hiraku registry import remote-registry.yaml            # preview
hiraku registry import remote-registry.yaml --apply --reviewed-hash <content_sha256>
```

Descriptor-only registration never opens the artifact location, so the remote
URI can point anywhere. Credentials in an `access` mapping, such as a `token`
key or a `user:password@` part of a URI, are redacted before anything is
stored.

## Browse

```bash
hiraku dashboard
```

The **Datasets** section lists every registered artifact with its source,
revision, observation time, coverage, availability, and documentation links,
independent of workflow runs.

## Unchanged reimport

Commit the records, then import the same manifest again. Nothing is edited and
`git status` stays clean:

```bash
git add -A && git commit -m "register datasets"
hiraku registry import remote-registry.yaml --apply --reviewed-hash <content_sha256>
git status --short                                    # nothing
```

## Changed revision

Changing a local file changes its `size:mtime` revision. The new scan has a new
hash, so preview it again before applying:

```bash
echo "P3,S4,scRNA,pass_1" >> data/measurements.csv
hiraku registry scan --root data            # Update: 1, new content_sha256
hiraku registry scan --root data --apply --reviewed-hash <content_sha256>
```

The record's `registration.history` keeps a summary of the replaced
observation.

## Partial refresh

`partial-refresh.yaml` reports `data/notes.txt` with `coverage: partial`. A
partial manifest merges evidence without claiming absence, so
`data/measurements.csv` stays `present`:

```bash
hiraku registry import partial-refresh.yaml --apply --reviewed-hash <content_sha256>
```

`data/notes.txt` doesn't exist on disk. The next exhaustive scan of `data/` is
authoritative for that root, so it demotes the file to `stale` and keeps it in
the record:

```bash
hiraku registry scan --root data            # Update: 1
hiraku registry scan --root data --apply --reviewed-hash <content_sha256>
```

## Records

Each dataset is a normal Hiraku record of `type: dataset`:

```bash
hiraku record list --json
hiraku record show <dataset-id> --json
```

Its `registration` front matter is readable by main's Hiraku without any
registry code.
