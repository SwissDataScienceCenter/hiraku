# Autoresearch with unified records

Start from the upstream repository:

```bash
git clone https://github.com/karpathy/autoresearch.git
cd autoresearch
hiraku init --example autoresearch
```

The overlay adds a bounded dispatcher, a CPU mock for testing, a research
program, and a reusable `task.md` objective template. It does not overwrite
upstream `README.md`, `train.py`, or `program.md`.

Keep local Hiraku state and run artifacts out of implementation commits if
that matches your branch workflow:

```gitignore
/hiraku/*
/hiraku.yml
/.agents/
/.claude/
/runs.csv
/run.log
```

Create a root record for the overall effort. A record may name the working
branch and shared entrypoints; the CLI does not create the Git branch itself.

```bash
git switch -c autoresearch/sep10
hiraku record create sep10 --type task --markdown-file task.md \
  --set 'branch=autoresearch/sep10' \
  --set 'goal=Minimize val_bpb within a fixed five-minute budget' \
  --set 'metrics=[{name: val_bpb, direction: minimize}]' \
  --set 'entrypoints={train: {command: [uv, run, python, train.py]}, mock: {command: [python, cpu_mock.py]}}'
hiraku record show sep10 --json
```

Each attempt is a child record with its own commit. The mock uses the same
dispatcher and CSV attribution without starting GPU training:

```bash
hiraku record create cpu-test --parent sep10 --type experiment --commit HEAD
hiraku launch mock --record cpu-test -- --val-bpb 0.9
hiraku runs --record cpu-test --json
hiraku record update cpu-test --set decision=discard
```

The dispatcher writes `record_id`, commit, Git state, status, and metrics to
`runs.csv`. `hiraku runs --record sep10 --descendants --json` compares all
attempts under the research root.
