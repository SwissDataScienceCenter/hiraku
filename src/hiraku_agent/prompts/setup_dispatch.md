# Task: choose and configure how this project launches experiments

`hiraku launch` hands every experiment to a **dispatcher**, a script in
`hiraku/dispatch/`. You are choosing that dispatcher for this project and filling in
its site settings. You do not write files: you return JSON and the tool writes it.

## The contract every dispatcher must keep

- It is called as `NAME.sh [--commit REF] -- COMMAND...` from the project root, with
  `HIRAKU_RECORD_ID`, `HIRAKU_RECORD_FILE`, `HIRAKU_ROOT` and `HIRAKU_ENTRYPOINT` set.
- With `--commit REF`, COMMAND runs on exactly REF's code (a checkout or worktree of
  REF), never on whatever happens to be checked out.
- COMMAND sees `HIRAKU_COMMIT` (the full hash it runs) and `HIRAKU_GIT_STATE`
  (`clean` or `dirty`), and inherits the four variables above.
- A dispatcher that only *submits* the work (a scheduler, a remote cluster) returns
  once it is queued, prints the job id as the last line of stdout, and runs
  `hiraku-agent mark-running "$HIRAKU_RECORD_ID" --job-id ID` when `hiraku-agent` is on
  PATH (for Slurm job ids; for other systems use `--check 'COMMAND'`, a command that
  exits 0 while the work is alive).
- Its exit status is non-zero when launching failed.

The tool checks this contract afterwards (`hiraku-agent check-dispatch`), so a
dispatcher that skips any of it will be reported, not silently used.

## Templates (prefer them)

- `worktree.sh`: runs the exact commit in a temporary worktree on this machine and
  waits for it. For a project that runs locally.
- `slurm.sh`: submits the exact commit as a Slurm batch job from a worktree under the
  Git directory and returns at submission. Site settings are `#SBATCH` lines you
  return in `sbatch_lines` (account, partition, time, nodes, GPUs, CPUs, memory,
  container environment). Do not include `--job-name` or `--output` unless the
  project clearly wants its own; the template sets them.
- `launch.sh`: runs the current checkout as it is and ignores `--commit`. Only for
  manual debugging; never choose it as the project's dispatcher.

Choose a template whenever one fits. Write your own `script` only for a backend no
template covers (Ray, Kubernetes, SkyPilot, PBS, LSF, a remote host over ssh, ...), and
then keep every point of the contract; start from how `worktree.sh` creates and
removes a worktree of the exact commit.

## How to decide

Use, in this order: the researcher's instruction below; the tools found on this
machine (`tools_on_path`); how the project already launches work (`files` and
`excerpts`: existing `.sbatch` scripts, cluster configs, job manifests). Take site
settings from those files and say which file each came from. If several existing
scripts disagree (different time limits or GPU counts), take the settings they share
and the most common value otherwise, and say so in `notes`. Never invent an account,
a partition or a cluster address: if no file or instruction names one, leave it out
and say in `notes` what the researcher must add.

## Current state

```json
{{state}}
```

{{instruction}}

## Output contract

Return ONE JSON object and nothing else.

```json
{
  "backend": "local|slurm|ray|kubernetes|skypilot|pbs|lsf|other",
  "template": "worktree.sh|slurm.sh|null (null when you wrote a script)",
  "name": "file name to write, e.g. slurm.sh, or ray.sh for your own script",
  "sbatch_lines": ["#SBATCH --account=...", "#SBATCH --time=..."],
  "script": "the complete dispatcher, starting with #!/bin/sh, or null with a template",
  "sources": [{"setting": "--account", "file": "path it was read from"}],
  "notes": "what was chosen and why, and anything the researcher must still add"
}
```
