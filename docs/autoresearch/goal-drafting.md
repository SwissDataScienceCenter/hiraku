# Drafting a goal, and giving the agent your project's memory

Two things make the difference between a goal record you accept and one you rewrite: whether the
agent has read your project, and whether it is honest about what it could not work out.

## The problem this solves

`hiraku-auto goal add` gives you a template. The template asks for an evaluation protocol — the
dataset and its checksum, the splits and how they were produced, the exact definition of the metric,
the seeds, and what is held fixed. Those five things are inherited unchanged by every experiment
under the goal, and gate condition 4 compares each run against them. A goal whose protocol is still
template prose therefore produces experiments that can never be promoted, however well they run.

Filling that in by hand, from nothing, is a lot to ask before the first experiment exists. But it is
rarely *from nothing*: the metric definition is usually in `CLAUDE.md`, the dataset path is in a
README, the split convention is in a docs page. The agent just has not been shown any of it.

## `hiraku-auto context` — what the agent gets to read

```bash
hiraku-auto context                    # exactly what would be sent, with sizes
hiraku-auto context --add src/vo.py    # try adding a file
hiraku-auto context --json             # the bundle as the agent sees it
```

The sources come from `context.sources` in `research/config.md`:

```yaml
context:
  sources:
    - CLAUDE.md
    - AGENTS.md
    - README.md
    - docs/**/*.md
    - notes/**/*.md
    - research/config.md
  max_files: 40
  max_bytes_per_file: 20000
  max_total_bytes: 200000
```

Points worth knowing:

- **It is prompt content.** These excerpts go to whatever model `--agent-cmd` names. Run
  `hiraku-auto context` and read the list before the first call; trim `context.sources` if anything
  there should not leave the machine. `context: {sources: []}` turns it off entirely.
- **Source trees are not swept.** Only the patterns you list. Name a specific module with
  `--context src/pipeline.py` when the code itself is what needs reading.
- **Sources may point outside the project.** This is the normal case: the research project holds
  the records, and the code it studies is a separate checkout. Both forms work —

  ```yaml
  context:
    sources:
      - CLAUDE.md                                # this project
      - ../mapillary_alignment/CLAUDE.md         # the code repository next door
      - ../mapillary_alignment/docs/**/*.md
      - /Users/you/PycharmProjects/other/README.md
  ```

  and `--context ../mapillary_alignment/CLAUDE.md` does it for one call. Outside files are marked
  `another repo` in `hiraku-auto context` and labelled `../mapillary_alignment/CLAUDE.md` in the
  prompt, so it stays obvious which repository a definition came from — and your home directory
  never ends up in a prompt.
- **Credential locations are refused** wherever a pattern lands on them: `.ssh`, `.gnupg`, `.aws`,
  `.env`, `.netrc`, keyrings and the like. So is naming a bare home or filesystem root, which a
  stray `**` would otherwise sweep.
- **Records are excluded** even if a glob would match them. `research/goals/`, `research/experiments/`
  and `research/proposals/` are already in the state at full length; sending them twice wastes the
  budget and invites the agent to reason from the stale copy.
- **Bounded, and truncation is visible.** A file over the per-file cap is cut and labelled, not
  silently dropped. Environments, caches and `.git` are never walked.

Which workflows read it:

| workflow | context by default | why |
|---|---|---|
| `draft` | yes | it is writing the terms everything else inherits |
| `design` | yes | proposing experiments in the project's own vocabulary |
| `discover` | yes | judging whether an outside idea applies *here* |
| `triage` | no | the run table is the evidence; prose is dead weight |
| `analyze` | no | same |

`--context PATH` adds a file to any of them (and switches it on for `triage`/`analyze`);
`--no-context` switches it off.

## `hiraku-auto goal draft` — the agent writes the record

```bash
hiraku-auto goal draft vggt-dinov3 --question "currently using VGGT for reprojection between the
crowd-sourced Mapillary images and the Natix ego video is not good, especially between Mapillary
images and Natix video frames. We suspect the domain gap. Would DINOv3 help, and how would it
combine into the VGGT pipeline?"
```

You write the question the way you would say it. The agent reads the question, the project context
and any other goals, and returns a complete record: title, primary metric, direction, target, gate
metric, the question restated precisely, why it matters, the success criterion, the five-part
evaluation protocol, what is out of scope, and open threads. The tool writes it; you review it.

Useful flags:

- `--question-file PATH`, or pipe the question on stdin.
- `--revise` — redraft an existing goal's sections, keeping its History. Use it after filling in
  some unset values, to have the rest rewritten around them.
- `--with-experiments` — also create the suggested first experiments as `proposed` nodes. Off by
  default on purpose: proposing experiments before the protocol is settled produces work that
  cannot be promoted.
- `--dry-run` — print the prompt and call nothing. Worth doing once, to see what is being sent.

## `**[UNSET: ...]**` — the honest gap

The agent will not be able to ground everything. Dataset ids, checksums and internal conventions are
often written down nowhere. Where that happens it writes

```markdown
### Dataset and version

**[UNSET: dataset version and checksum — CLAUDE.md names no Mapillary slice id or Natix clip id]**
```

rather than a plausible sentence. This is the point of the whole feature. A guess and a fact look
identical once they are in the record; a marker cannot be mistaken for either. The marker says what
is needed *and* what the agent looked for, so filling it in is a lookup rather than a decision.

They surface in three places:

```
$ hiraku-auto goal show vggt-dinov3
...
3 value(s) still unset — fill these in before running:
  [Evaluation protocol] dataset version and checksum — CLAUDE.md names no Mapillary slice id ...
  [Evaluation protocol] split definition — docs/splits.md says frames 0-2699 evaluate but not ...
  [Evaluation protocol] seed count — nothing in the project files states one

$ hiraku-auto check
vggt-dinov3: unset in 'Evaluation protocol' — dataset version and checksum — ...

$ hiraku-auto agent design vggt-dinov3
warning: goal 'vggt-dinov3' has 3 unset value(s) in its record. Gate condition 4
         compares a run against this goal's evaluation protocol, so anything designed now
         may be unpromotable.
```

You replace them by editing the record. That is a normal text edit; nothing regenerates it.

## The other half of the same rule: a name with no formula

`**[UNSET: ...]**` catches a value nobody supplied. This catches its quieter twin — a metric name
used in a sentence that reads as precise, where nothing in the record says what the number is.

```
$ hiraku-auto check
cross-domain: metric `pairs_at_cap_fraction` is used in Success criterion but never defined
              in the Evaluation protocol
```

It fires on any `lower_snake_case` name in backticks, anywhere in a goal record — the question, the
success criterion, out of scope, open threads, or the `primary_metric` / `gate_metric` front matter
— that never appears in `## Evaluation protocol`. The tool's own vocabulary (`run_kind`,
`agreement_tolerance`, `primary_metric` and the rest) is excluded.

Why the protocol specifically: it is the one section every experiment under the goal inherits. A
definition written anywhere else is not inherited, so it settles nothing.

`goal add` now writes each declared metric into `### Metrics` with an UNSET beside it, so a fresh
goal tells you which formulas it is waiting for instead of quietly using names nothing defines:

```markdown
### Metrics

- `cross_source_reprojection_error_px` — **[UNSET: the formula for
  `cross_source_reprojection_error_px` — the reduction (mean/median), the unit, and what is
  excluded. Those three choices change the number by more than most methods do.]**
```

## The whole first step, end to end

```bash
# 1. make sure the project describes itself somewhere the agent will look
hiraku-auto context

# 2. draft the goal from your question
hiraku-auto goal draft vggt-dinov3 --question "..." \
  --agent-cmd 'claude -p --allowedTools WebSearch,WebFetch'

# 3. read it, and replace every UNSET value
hiraku-auto goal show vggt-dinov3
$EDITOR research/goals/vggt-dinov3.md
hiraku-auto check        # clean means the protocol has no holes

# 4. now the tree
hiraku-auto agent design vggt-dinov3
```

Step 3 is the one that cannot be automated away, and it is short: the agent has already told you
which values it needs and why. What it must not do is invent them, because an invented protocol is
worse than no protocol — every run measured against it looks comparable and is not.
