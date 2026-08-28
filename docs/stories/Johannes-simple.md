# Johannes: Simple, linear research on one branch

The main goal of the simple workflow is to add explicit research metadata without changing an ordinary sequential Git workflow. A Task defines the goal and review surface, each Experiment links a hypothesis to one exact commit and its Runs, and the tracking backend stores execution evidence. All implementation work happens on one branch—either the task branch or, by project convention, `main`—with no parallel attempts or experiment branches.

## Workflow

```bash
# 1. Create the Task and continue all work on its single branch.
hiraku task create heart-disease --template guided
git switch -c hiraku/task/heart-disease

# 2. Define the goal, fixed evaluation protocol, implementation plan,
# metrics, and permitted entrypoints; review them before implementation.
/hiraku "define the fixed heart-disease evaluation protocol"
$EDITOR tasks/heart-disease/task.md
$EDITOR tasks/heart-disease/launch.yml

# 3. Create one Experiment for a coherent, testable attempt.
hiraku experiment create heart-disease:logistic-baseline \
  --summary "Logistic regression baseline" \
  --hypothesis "Regularized logistic regression provides a calibrated baseline." \
  --entrypoint logistic

# 4. Implement, review, commit, and bind the Experiment to that exact snapshot.
/hiraku "implement the approved logistic baseline"
git diff
git add <implementation-files>
git commit -m "implement logistic baseline"
hiraku experiment set heart-disease:logistic-baseline --commit HEAD

# 5. Launch reproducible Runs and compare their tracked metrics.
hiraku launch --experiment logistic-baseline heart-disease:logistic
hiraku runs heart-disease --sort val/log_loss --ascending

# 6. Record the conclusion before starting the next sequential Experiment.
/hiraku "conclude logistic-baseline and record the decision"

hiraku experiment create heart-disease:random-forest \
  --summary "Random forest comparison" \
  --hypothesis "Nonlinear interactions improve validation log loss." \
  --entrypoint random-forest
/hiraku "implement the approved random-forest experiment"
git add <implementation-files>
git commit -m "implement random-forest experiment"
hiraku experiment set heart-disease:random-forest --commit HEAD
hiraku launch --experiment random-forest heart-disease:random-forest

# 7. Synthesize reviewed evidence and decisions in the canonical Task record.
hiraku runs heart-disease --sort val/log_loss --ascending
/hiraku "summarize the reviewed experiments in task.md"
$EDITOR tasks/heart-disease/task.md
```

## Preferred user interfaces

The repository-local agent skill guides the research checkpoints, while the CLI creates records, performs reproducible launches, and compares Runs. `task.md` is the primary review artifact; Experiment YAML or Markdown files provide inspectable supporting records. The read-only dashboard is an optional task-level overview, not a required part of the linear workflow.
