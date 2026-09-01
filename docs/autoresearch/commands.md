# Command reference

Generated from the CLI parser by `tools/gen_commands_doc.py`. Do not edit by hand.

`--project <path>` works on every command and points at a project without `cd`-ing into it;
without it the project is found by walking up from the working directory to the nearest
`.hiraku.yml`.

Guides: [cli.md](cli.md) for the walkthrough and the environment/container setup,
[research-loop.md](research-loop.md) for what the triage and promotion rules mean.

## At a glance

| Command | Does |
|---|---|
| `init <path> --git` | create a research project, in its own repository |
| `goal add\|list\|show` | research questions: metric, direction, target, gate metric |
| `exp create\|list\|show\|set` | experiments; `--parent` is what makes one a sub-experiment |
| `tree` | the tree, from declared `parent` links |
| `check` | every consistency problem the records can express |
| `sync` | regenerate the Children sections |
| `launch <exp>:<entrypoint>` | run it, record provenance and metrics |
| `runs` | list or rank runs |
| `agent design\|analyze\|triage\|discover` | call Claude Code; returns JSON, tool writes |
| `next` | what the loop would do next, without calling anything |
| `loop` | bounded loop, stopping wherever a human decides |
| `promote --run <id>` | the five-condition gate |
| `report <goal>` | assemble the goal's report from the records |
| `proposal list\|accept\|decline` | the agent's pending queue |
| `worktree add\|remove\|list` | one checkout per active experiment |
| `dash` / `ui` | the static page / the Streamlit app |

## Full help

### `hiraku-auto --help`
```
usage: hiraku-auto [-h] [--project PROJECT]
                   {init,goal,exp,tree,check,sync,context,tracking,logs,launch,runs,agent,next,loop,promote,proposal,worktree,report,dash,ui}
                   ...

Autoresearch over a Hiraku experiment tree: goals, parallel hypotheses,
failure triage, a gated leaderboard, and an agent that designs and analyses.

positional arguments:
  {init,goal,exp,tree,check,sync,context,tracking,logs,launch,runs,agent,next,loop,promote,proposal,worktree,report,dash,ui}
    init                create a research project (in its own directory)
    goal                research questions: the metric, the target, the
                        evaluation protocol
    exp                 experiments; a sub-experiment is an experiment with a
                        --parent
    tree                render the experiment tree
    check               validate every record and the run table
    sync                regenerate Children sections from parent links
    context             list the project files the agent would be given,
                        before sending any
    tracking            where runs are recorded, and whether the MLflow mirror
                        is reachable
    logs                every agent call: the prompt sent, the reply, how long
    launch              run an entrypoint and record the run
    runs                list or rank runs
    agent               call Claude Code to design, triage, discover or
                        analyse
    next                what the loop would do next (no agent call)
    loop                bounded loop; stops wherever a human decides
    promote             put a run through the promotion gate
    proposal            agent proposals awaiting a decision
    worktree            one checkout per active experiment
    report              assemble a goal's report from the records: known,
                        ruled out, still open
    dash                write the HTML tree viewer
    ui                  run the Streamlit interface

options:
  -h, --help            show this help message and exit
  --project PROJECT     project directory (default: search upward for
                        .hiraku.yml)
```

### `hiraku-auto init --help`
```
usage: hiraku-auto init [-h] [--name NAME] [--git] [--force]
                        [--allow-in-tool-repo]
                        [path]

positional arguments:
  path                  created if it does not exist

options:
  -h, --help            show this help message and exit
  --name NAME           project name written into research/config.md
  --git                 also run `git init` if needed
  --force               fill in missing files; never overwrites
  --allow-in-tool-repo  permit init inside the hiraku-auto source tree (you
                        almost never want this)
```

### `hiraku-auto goal --help`
```
usage: hiraku-auto goal [-h] {add,draft,list,show,tidy,rm} ...

positional arguments:
  {add,draft,list,show,tidy,rm}
    add                 create a goal
    draft               write the goal record with the agent, from a question
                        and the project's own files
    list                one row per goal, with node and run counts
    show                print a goal record
    tidy                collapse repeated `goal redrafted` entries and stale
                        grounding blocks in History
    rm                  delete a goal record; refuses while experiments or
                        runs depend on it

options:
  -h, --help            show this help message and exit
```

### `hiraku-auto goal add --help`
```
usage: hiraku-auto goal add [-h] --metric METRIC
                            [--direction {minimize,maximize}]
                            [--target TARGET] [--gate-metric GATE_METRIC]
                            [--question QUESTION] [--history] [--no-history]
                            id

positional arguments:
  id

options:
  -h, --help            show this help message and exit
  --metric METRIC       primary metric name
  --direction {minimize,maximize}
  --target TARGET       the value that would settle the question
  --gate-metric GATE_METRIC
                        a metric that must not degrade (e.g. completion_rate)
  --question QUESTION
  --history             keep a History section in the record (off by default;
                        git already has the history)
  --no-history          never keep one, whatever config.md says
```

### `hiraku-auto goal draft --help`
```
usage: hiraku-auto goal draft [-h] [--question QUESTION]
                              [--question-file PATH] [--revise] [--history]
                              [--no-history] [--with-experiments]
                              [--instruction INSTRUCTION]
                              [--instruction-file PATH] [--context PATH]
                              [--no-context] [--context-bytes N]
                              [--agent-cmd AGENT_CMD] [--unset-env VAR]
                              [--dry-run] [--no-apply] [--timeout TIMEOUT]
                              id

positional arguments:
  id

options:
  -h, --help            show this help message and exit
  --question QUESTION   the research question, in your own words
  --question-file PATH  read the question from a file
  --revise              rewrite an existing goal's drafted sections; anything
                        you edited by hand is lost
  --history             keep a History section in the record (off by default;
                        git already has the history)
  --no-history          never keep one, whatever config.md says
  --with-experiments    also create the agent's suggested first experiments,
                        as proposed nodes
  --instruction INSTRUCTION
                        extra context for the agent
  --instruction-file PATH
                        read it from a file — where your own ideas go, one
                        paragraph each; `discover` works them up into
                        proposals with origin `researcher`
  --context PATH        an extra file or glob to send along; repeatable
  --no-context          send no project files at all
  --context-bytes N     raise the per-file cap for this call; see `hiraku-auto
                        context`
  --agent-cmd AGENT_CMD
                        default: 'claude -p'
  --unset-env VAR       drop this variable from the agent's environment;
                        repeatable. ANTHROPIC_API_KEY is the usual one — it
                        overrides your claude.ai login.
  --dry-run             print the prompt, call nothing
  --no-apply            call the agent but write no files
  --timeout TIMEOUT     seconds; default agent.timeout, else 900
```

### `hiraku-auto goal list --help`
```
usage: hiraku-auto goal list [-h] [--json]

options:
  -h, --help  show this help message and exit
  --json      machine-readable output
```

### `hiraku-auto goal show --help`
```
usage: hiraku-auto goal show [-h] [--json] id

positional arguments:
  id

options:
  -h, --help  show this help message and exit
  --json      front matter and sections as JSON
```

### `hiraku-auto goal tidy --help`
```
usage: hiraku-auto goal tidy [-h] [--dry-run] [--history] [--no-history] [id]

positional arguments:
  id            one goal; omit for every goal

options:
  -h, --help    show this help message and exit
  --dry-run     say what would go, write nothing
  --history     keep the section, collapsing repeats to one line per date,
                instead of removing it
  --no-history  remove it, whatever config.md says
```

### `hiraku-auto goal rm --help`
```
usage: hiraku-auto goal rm [-h] [--with-experiments] [--force] id

positional arguments:
  id

options:
  -h, --help          show this help message and exit
  --with-experiments  delete the goal's experiment records too (branches and
                      worktrees are kept)
  --force             delete even though runs were recorded; the run rows stay
                      and become orphans
```

### `hiraku-auto exp --help`
```
usage: hiraku-auto exp [-h] {create,list,brief,show,set,rm} ...

positional arguments:
  {create,list,brief,show,set,rm}
    create              create an experiment and its branch
    list                one row per experiment
    brief               the spec for writing this experiment's code:
                        expectation, run contract, protocol
    show                print a record and its runs
    set                 edit front matter on an existing record
    rm                  delete an experiment record; refuses while children or
                        runs depend on it

options:
  -h, --help            show this help message and exit
```

### `hiraku-auto exp create --help`
```
usage: hiraku-auto exp create [-h] [--goal GOAL] [--parent PARENT]
                              [--kind {baseline,hypothesis,diagnostic,ablation,combination}]
                              [--mode {guided,auto}]
                              [--status {proposed,approved,running,concluded,abandoned}]
                              [--origin ORIGIN] [--expectation EXPECTATION]
                              [--branch BRANCH] [--no-branch] [--worktree]
                              id

positional arguments:
  id

options:
  -h, --help            show this help message and exit
  --goal GOAL
  --parent PARENT       the node whose result this responds to
  --kind {baseline,hypothesis,diagnostic,ablation,combination}
  --mode {guided,auto}
  --status {proposed,approved,running,concluded,abandoned}
  --origin ORIGIN       researcher | agent-diagnostic | agent-literature
  --expectation EXPECTATION
                        one falsifiable sentence; required before launching
  --branch BRANCH       default: exp/<id>; must be a flat name
  --no-branch           write the record only
  --worktree            also add .worktrees/<id>
```

### `hiraku-auto exp list --help`
```
usage: hiraku-auto exp list [-h] [--goal GOAL] [--json]

options:
  -h, --help   show this help message and exit
  --goal GOAL  restrict to one goal
  --json       machine-readable output
```

### `hiraku-auto exp brief --help`
```
usage: hiraku-auto exp brief [-h] [--out PATH] id

positional arguments:
  id

options:
  -h, --help  show this help message and exit
  --out PATH  write it to a file to hand to a coding agent
```

### `hiraku-auto exp show --help`
```
usage: hiraku-auto exp show [-h] [--json] id

positional arguments:
  id

options:
  -h, --help  show this help message and exit
  --json      record, lineage and runs as JSON
```

### `hiraku-auto exp set --help`
```
usage: hiraku-auto exp set [-h]
                           [--status {proposed,approved,running,concluded,abandoned}]
                           [--verdict {supports,refutes,inconclusive}]
                           [--kind {baseline,hypothesis,diagnostic,ablation,combination}]
                           [--mode {guided,auto}] [--goal GOAL]
                           [--parent PARENT] [--origin ORIGIN]
                           [--expectation EXPECTATION] [--clear KEY [KEY ...]]
                           id

positional arguments:
  id

options:
  -h, --help            show this help message and exit
  --status {proposed,approved,running,concluded,abandoned}
  --verdict {supports,refutes,inconclusive}
                        only meaningful when concluded
  --kind {baseline,hypothesis,diagnostic,ablation,combination}
  --mode {guided,auto}  may narrow the parent's, never widen it
  --goal GOAL
  --parent PARENT       reparent: one line, no files move
  --origin ORIGIN
  --expectation EXPECTATION
  --clear KEY [KEY ...]
                        remove these keys
```

### `hiraku-auto exp rm --help`
```
usage: hiraku-auto exp rm [-h] [--with-children] [--force] id

positional arguments:
  id

options:
  -h, --help       show this help message and exit
  --with-children  delete the whole subtree beneath it as well
  --force          delete even though runs were recorded; the run rows stay
                   and become orphans
```

### `hiraku-auto tree --help`
```
usage: hiraku-auto tree [-h] [--goal GOAL] [--json]

options:
  -h, --help   show this help message and exit
  --goal GOAL  one goal only
  --json       full snapshot: the same one the agent and viewers read
```

### `hiraku-auto check --help`
```
usage: hiraku-auto check [-h] [--strict]

options:
  -h, --help  show this help message and exit
  --strict    exit non-zero when problems are found
```

### `hiraku-auto sync --help`
```
usage: hiraku-auto sync [-h]

options:
  -h, --help  show this help message and exit
```

### `hiraku-auto context --help`
```
usage: hiraku-auto context [-h] [--add PATH] [--context-bytes N] [--json]

options:
  -h, --help         show this help message and exit
  --add PATH         also resolve this file or glob
  --context-bytes N  raise the per-file cap for this call (default:
                     context.max_bytes_per_file)
  --json             the bundle exactly as the agent sees it
```

### `hiraku-auto tracking --help`
```
usage: hiraku-auto tracking [-h] [--experiment EXPERIMENT] [--goal GOAL]
                            [--json]

options:
  -h, --help            show this help message and exit
  --experiment EXPERIMENT
                        resolve as it would be for this experiment
  --goal GOAL           name the goal, when no experiment is given
  --json                machine-readable output
```

### `hiraku-auto logs --help`
```
usage: hiraku-auto logs [-h] [--limit LIMIT] [--show ID] [--prompt] [--json]

options:
  -h, --help     show this help message and exit
  --limit LIMIT  how many calls to list
  --show ID      print one call in full; 'latest' for the newest
  --prompt       with --show, print the prompt instead of the reply
  --json         machine-readable output
```

### `hiraku-auto launch --help`
```
usage: hiraku-auto launch [-h] [--commit REF] [--seed SEED] [--dry-run]
                          EXPERIMENT:ENTRYPOINT

positional arguments:
  EXPERIMENT:ENTRYPOINT

options:
  -h, --help            show this help message and exit
  --commit REF          run this commit in a temporary worktree (reproducible)
  --seed SEED           recorded, and exported as HIRAKU_SEED
  --dry-run             print what would run; execute nothing
```

### `hiraku-auto runs --help`
```
usage: hiraku-auto runs [-h] [--sort METRIC] [--ascending] [--descending]
                        [--json]
                        [experiment]

positional arguments:
  experiment     omit for every run in the project

options:
  -h, --help     show this help message and exit
  --sort METRIC  rank by a numeric column
  --ascending    lower is better
  --descending   higher is better
  --json         machine-readable output
```

### `hiraku-auto agent --help`
```
usage: hiraku-auto agent [-h] [--instruction INSTRUCTION]
                         [--instruction-file PATH] [--context PATH]
                         [--no-context] [--context-bytes N]
                         [--agent-cmd AGENT_CMD] [--unset-env VAR] [--dry-run]
                         [--no-apply] [--show-raw] [--timeout TIMEOUT]
                         {draft,design,triage,discover,analyze} target

positional arguments:
  {draft,design,triage,discover,analyze}
  target                goal id for design/discover; experiment id for
                        triage/analyze

options:
  -h, --help            show this help message and exit
  --instruction INSTRUCTION
                        extra context for the agent
  --instruction-file PATH
                        read it from a file — where your own ideas go, one
                        paragraph each; `discover` works them up into
                        proposals with origin `researcher`
  --context PATH        an extra project file or glob to send along;
                        repeatable
  --no-context          send no project files (design and discover send them
                        by default)
  --context-bytes N     raise the per-file cap for this call; see `hiraku-auto
                        context`
  --agent-cmd AGENT_CMD
                        default: 'claude -p'
  --unset-env VAR       drop this variable from the agent's environment;
                        repeatable. ANTHROPIC_API_KEY is the usual one — it
                        overrides your claude.ai login.
  --dry-run             print the prompt, call nothing
  --no-apply            call the agent but write no files
  --show-raw            also print the raw reply
  --timeout TIMEOUT     seconds; default agent.timeout, else 900
```

### `hiraku-auto next --help`
```
usage: hiraku-auto next [-h] [--json] goal

positional arguments:
  goal

options:
  -h, --help  show this help message and exit
  --json      machine-readable output
```

### `hiraku-auto loop --help`
```
usage: hiraku-auto loop [-h] [--max-iterations MAX_ITERATIONS]
                        [--max-parallel N] [--gpus SPEC] [--launch ENTRYPOINT]
                        [--commit COMMIT] [--seed SEED]
                        [--agent-cmd AGENT_CMD] [--timeout TIMEOUT]
                        goal

positional arguments:
  goal

options:
  -h, --help            show this help message and exit
  --max-iterations MAX_ITERATIONS
                        hard cap on agent calls
  --max-parallel N      launch up to N approved experiments at once (default
                        1, i.e. sequential)
  --gpus SPEC           '8' for devices 0-7, or an explicit list '0,1,4,5';
                        one device per slot via CUDA_VISIBLE_DEVICES
  --launch ENTRYPOINT   allow the loop to launch this entrypoint
  --commit COMMIT       ref for launches the loop makes; default is each
                        experiment's own branch
  --seed SEED
  --agent-cmd AGENT_CMD
                        default: 'claude -p'
  --timeout TIMEOUT     seconds; default agent.timeout, else 900
```

### `hiraku-auto promote --help`
```
usage: hiraku-auto promote [-h] --run RUN_ID [--signed-off-by NAME] [--force]
                           experiment

positional arguments:
  experiment

options:
  -h, --help            show this help message and exit
  --run RUN_ID
  --signed-off-by NAME  gate condition 5, in guided mode
  --force               record it anyway, marked as forced
```

### `hiraku-auto proposal --help`
```
usage: hiraku-auto proposal [-h] {list,accept,decline} ...

positional arguments:
  {list,accept,decline}
    list                the pending queue
    accept              turn a proposal into an experiment
    decline             record a decision; the file is kept

options:
  -h, --help            show this help message and exit
```

### `hiraku-auto proposal list --help`
```
usage: hiraku-auto proposal list [-h] [--json]

options:
  -h, --help  show this help message and exit
  --json      machine-readable output
```

### `hiraku-auto proposal accept --help`
```
usage: hiraku-auto proposal accept [-h] [--mode {guided,auto}] id

positional arguments:
  id

options:
  -h, --help            show this help message and exit
  --mode {guided,auto}
```

### `hiraku-auto proposal decline --help`
```
usage: hiraku-auto proposal decline [-h] [--reason REASON] id

positional arguments:
  id

options:
  -h, --help       show this help message and exit
  --reason REASON  why — it counts against novelty later
```

### `hiraku-auto worktree --help`
```
usage: hiraku-auto worktree [-h] [--force] {add,remove,list} [id]

positional arguments:
  {add,remove,list}
  id                 experiment id; omit for `list`

options:
  -h, --help         show this help message and exit
  --force            remove even with local changes
```

### `hiraku-auto report --help`
```
usage: hiraku-auto report [-h] [-o FILE] goal

positional arguments:
  goal

options:
  -h, --help           show this help message and exit
  -o FILE, --out FILE  write to a file instead of stdout
```

### `hiraku-auto dash --help`
```
usage: hiraku-auto dash [-h] [-o OUT]

options:
  -h, --help         show this help message and exit
  -o OUT, --out OUT  output file
```

### `hiraku-auto ui --help`
```
usage: hiraku-auto ui [-h] [--port PORT]

options:
  -h, --help   show this help message and exit
  --port PORT  Streamlit port
```
