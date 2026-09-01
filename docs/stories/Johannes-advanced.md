# Johannes: Parallel research with experiment branches

The main goal of the advanced workflow is to preserve the same Task–Experiment–Run metadata model while supporting parallel exploration and backtracking. A canonical task branch holds the shared goal and reviewed synthesis; each Experiment may use its own Git branch and exact commit. Independent agents or researchers can work in separate worktrees, and accepted implementations are explicitly integrated into the task branch. Git history supplies lineage, while semantic decisions remain in Hiraku records.

## Workflow

```bash
# 1. Create the Task and establish its canonical integration branch.
hiraku task create heart-disease --template guided
git switch -c hiraku/task/heart-disease
/hiraku "define the fixed heart-disease evaluation protocol"
$EDITOR tasks/heart-disease/task.md

# 2. Create independent Experiment branches from the task baseline.
# Hiraku records each derived branch and switches to it when possible.
hiraku experiment create heart-disease:logistic-baseline \
  --branch --from hiraku/task/heart-disease \
  --summary "Logistic regression baseline" \
  --entrypoint logistic

git switch hiraku/task/heart-disease
hiraku experiment create heart-disease:random-forest \
  --branch --from hiraku/task/heart-disease \
  --summary "Random forest comparison" \
  --entrypoint random-forest

# 3. Optionally check out the Experiment branches in separate worktrees so
# implementations and Runs can progress concurrently without branch switching.
git switch hiraku/task/heart-disease
git worktree add ../heart-logistic \
  hiraku/experiment/heart-disease/logistic-baseline
git worktree add ../heart-random-forest \
  hiraku/experiment/heart-disease/random-forest

# 4. In each worktree, implement and commit one coherent attempt.
cd ../heart-logistic
/hiraku "implement the approved logistic-baseline experiment"
git add <implementation-files>
git commit -m "implement logistic baseline"
hiraku experiment set heart-disease:logistic-baseline --commit HEAD

cd ../heart-random-forest
/hiraku "implement the approved random-forest experiment"
git add <implementation-files>
git commit -m "implement random-forest experiment"
hiraku experiment set heart-disease:random-forest --commit HEAD

# 5. Launch each Experiment from its bound commit. These executions may run
# concurrently; each uses an isolated committed snapshot and shared tracking.
hiraku launch --experiment random-forest heart-disease:random-forest
cd ../heart-logistic
hiraku launch --experiment logistic-baseline heart-disease:logistic

# 6. Compare Runs and record conclusions independently of Git integration.
hiraku runs heart-disease --sort val/log_loss --ascending
/hiraku "conclude the evaluated experiments and record their decisions"
hiraku experiment list heart-disease

# 7. Explicitly integrate accepted source history into the task branch.
# Acceptance alone never moves or mutates the task branch.
cd /path/to/project
git switch hiraku/task/heart-disease
git merge --no-ff hiraku/experiment/heart-disease/random-forest

# 8. Confirm exact ancestry, synthesize the reviewed findings, and update the
# pull request or merge request from the canonical task branch.
hiraku experiment list heart-disease
/hiraku "summarize the reviewed experiments in task.md"
hiraku review create heart-disease --push
hiraku dashboard heart-disease
```

## Preferred user interfaces

The CLI manages Task and Experiment records, commit-bound launches, lineage inspection, and review synchronization. Git branches and optional worktrees are the implementation and concurrency layer rather than hidden Hiraku state. `task.md` remains the canonical review document and complete pull-request or merge-request description; Experiment records, Run tables, and the read-only dashboard expose the detailed evidence behind it.
