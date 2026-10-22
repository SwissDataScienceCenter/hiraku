# Dataset registry

Hiraku can register existing datasets and their artifact descriptors as
ordinary research records. Registration **observes** data and documents its
source; it never executes workflows, fabricates runs, copies dataset contents,
computes scientific metrics, or infers historical production provenance.

A registered dataset is a normal Hiraku record (`type: dataset`). All registry
data lives under one namespaced, versioned front-matter key, so main's Hiraku
reads a registered project without any registry code installed.

## Schema

Registration data is stored under the `registration` key with `schema: 1`.

```yaml
id: dataset-8f2c9a1b
type: dataset
registration:
  schema: 1
  namespace: flagship-cohort          # identity namespace (the source scope)
  key: [sample-1, scrnaseq]           # source dataset key, stable across relocations
  metadata: {technology: scrnaseq}    # optional descriptive metadata
  coverage: exhaustive                # exhaustive | partial | unknown
  availability: present               # present | missing | unknown | stale (summary)
  observed_at: "2026-10-22T09:00:00+00:00"
  artifacts:
    - key: art-1                      # source artifact identity, not a path
      display_path: derived/pass_1/counts.csv   # project-relative POSIX path
      root: null                      # or a configured named root
      revision: "123:456"             # observation; null when unknown
      size: 100                       # bytes; null when unknown
      availability: present
      observed_at: "2026-10-22T09:00:00+00:00"
      access: {uri: "https://example.invalid/counts.csv"}  # never credentials
      documentation:
        - key: sop-abc
          role: output                # input | output | unknown
          applicability: dataset
  documentation: []                   # dataset-level evidence links
```

The models are `hiraku.registration.RegistrationManifest` (what a source
adapter emits), `hiraku.registration.Dataset` (a manifest entry), and
`hiraku.registration.Registration` (what a record stores). All three validate
their shapes; unknown technology, sample, or SOP fields are never required.

### Identity and hashes

- A dataset's identity is `(namespace, key)`. The `namespace` scopes the source
  key, so two sources using the same key never collide. The deterministic record
  ID is `dataset-<sha256(canonical_json([namespace, key]))[:20]>`.
- An artifact's identity is `(namespace, dataset key, artifact key)`. Identity
  never comes from a path, so relocating a mount does not create a new dataset
  or artifact.
- `canonical_json` sorts keys and uses compact separators; `content_digest`
  hashes a value after dropping `observed_at`, `managed`, and `history`.

### Timestamp semantics

`observed_at` is the source observation time, an ISO-8601 timestamp normalized
to UTC. Observation timestamps are **not** part of the semantic content digest,
so re-observing unchanged data is a no-op.

### Availability

`availability` distinguishes observations from record lifecycle state:

- `present` — observed at the location;
- `missing` — declared by an authoritative (exhaustive) source but absent;
- `stale` — observed before but not seen in a later exhaustive scan;
- `unknown` — not checked.

A dataset's `availability` summarizes its artifacts. `missing` and `stale` are
only meaningful under `coverage: exhaustive`; the schema rejects them under
partial or unknown coverage, because a partial snapshot cannot authoritatively
remove entries.

### Location, roots, and scope

Project artifacts use normalized project-relative POSIX `display_path` values,
the same form Snakemake reports in `jobs`. Artifacts under external named roots
store `root: <name>` plus a root-relative `display_path`. The manifest `scope`
records explicit `coverage`, adapter `filters`, `bounds`, and `roots`, so
coverage is never implied by a whole filesystem.

### Documentation links

`documentation` entries are evidence links with a stable `key`, a `role`
(`input`, `output`, or `unknown`), and an optional `applicability`. They are
not proof of production or of expected outputs, and they stay `unknown` when a
source does not support the distinction.

### Managed fields and revision history

`registration.managed` holds the importer's baselines of the top-level record
fields it owns, so a later import can detect human edits instead of silently
overwriting them. `registration.history` retains bounded past observations
(`observed_at`, `coverage`, and per-availability counts).

## Backward compatibility

Two earlier writers stored registry data as ordinary record extras. The
mapping in `hiraku.registration.compat` validates those shapes (rejecting
arbitrary dictionaries) and maps them to `Registration` once, explicitly:

| Legacy field | Registry field |
| --- | --- |
| `registry.cohort_id` | `namespace` |
| `registry.identity_inputs` (without the cohort) | `key` |
| `technology` | `metadata.technology` |
| `registry.scan_complete` | `coverage` |
| `files[].artifact_id` | `artifacts[].key` |
| `files[].path` | `artifacts[].display_path` |
| `files[].revision` | `artifacts[].revision` |
| `files[].size_bytes` | `artifacts[].size` |
| `files[].state == "file"` | `artifacts[].availability = present` |
| `files[].matches[].sop_id` | `artifacts[].documentation[].key` (`role: output`) |
| `missing_expected[]` | `artifacts[]` with `availability: missing`/`unknown` |
| `related_to` (SOP ids) | `documentation[]` (`role: unknown`) plus record links |
| `registry.importer_fields` | `managed` (normalized baselines) |
| `inventory_files[].path` | `artifacts[].display_path` |
| `inventory_files[].size_bytes` / `revision` | `artifacts[].size` / `revision` |
| `file_inventory.roots` | `metadata.roots` |
| `file_inventory.scan_complete` | `coverage` |

Deterministic legacy record IDs are preserved on migration: the writer matches
datasets by `(namespace, key)` identity rather than recomputing an ID, so
reimporting legacy cohort records never duplicates a dataset or loses human
text.
