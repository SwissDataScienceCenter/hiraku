# Optional Git setup

Hiraku initialization does not depend on Git:

```bash
hiraku clone heart-disease
cd heart-disease
```

To initialize the code repository at the same time, add `--git` to `hiraku init`.
Otherwise run `git init` whenever you need it. Metadata Git setup is independent
of initialization and can be run from any directory in the Hiraku project.

## Ignore local metadata

```bash
hiraku git setup ignore
```

This adds a managed block for `/hiraku/` and `/hiraku.yml` to the project-local
`.gitignore`, preserving existing rules. The configured `hiraku_dir` is used if
it differs from `hiraku`. The command also works without Git installed. Repeating
it does not duplicate rules.

The configuration file is ignored because selecting an active record changes it.
Agent configuration and run artifacts are not added to the managed ignore block;
manage their ignore rules according to the project and tracking backend.

## Independent local metadata history

```bash
hiraku git setup repo
git -C hiraku add .
git -C hiraku commit -m "Record initial investigation"
```

This initializes an independent Git repository inside `hiraku/` and ignores the
metadata and `hiraku.yml` in the code repository. No remote or initial commit is
created. Repeating setup preserves commits, local changes, and repository
configuration. You can add a remote later with ordinary Git commands.

Cloning the code repository does not copy this independent metadata history.
Back it up separately if you need to restore it elsewhere.

## Metadata submodule

With a code repository already initialized, create and commit independent metadata
history, then register it as a submodule:

```bash
hiraku git setup repo
git -C hiraku add .
git -C hiraku commit -m "Initialize research history"
hiraku git setup submodule --url ../project-records
```

`--url` records where the metadata can be cloned in future. It can be a local
repository path or a Git URL. Local paths are resolved from the calling directory and recorded as
absolute paths; they must remain accessible to restore the submodule. To share
the project across machines, use an accessible remote URL instead.

`hiraku/` must already be an independent repository with at least one commit;
uncommitted changes are allowed. Setup registers that repository without changing
its files or history. It performs no clone, fetch, merge, commit, or push, and does
not check whether the URL exists. Publish metadata commits to that location before
sharing a code commit that refers to them.

Setup stages `.gitmodules` and the submodule pointer. It sets `ignore = all` so
metadata edits and newer metadata commits do not appear as ordinary code changes.
`hiraku.yml` remains ignored in the code repository. No commits or pushes are made.
To publish a metadata snapshot, commit and push metadata first, then explicitly
stage the pointer with `git add --force hiraku` and commit it in the code repository.

New checkouts can restore a published snapshot with
`git submodule update --init`. Git normally restores submodules with detached HEAD;
switch to a metadata branch before adding new commits. Local source paths may need
an explicit per-command file transport allowance when restoring with Git, such as
`git -c protocol.file.allow=always submodule update --init`. Hiraku does not change
global Git settings.

## Existing projects and inspection

```bash
hiraku git status
hiraku git status --json
```

Status reports Git availability, the code repository, the metadata repository,
and the detected setup mode. It does not require a commit or a Git installation.

Setup refuses to silently untrack files that are already staged or committed in
the code repository. Migrate those files explicitly before choosing independent
metadata history. It also refuses to convert an existing submodule into another
mode or replace its URL implicitly. Repeating submodule setup with the same
URL succeeds without changing its pinned snapshot.

Submodule registration requires existing `.gitmodules` changes to be committed
first. Unrelated staged code changes are preserved.
