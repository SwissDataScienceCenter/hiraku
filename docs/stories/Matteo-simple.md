# Matteo: A first guided experiment for a beginner

A less experienced user should be able to use Hiraku without learning its
internal database, Git conventions, or autonomous-agent protocol. They start
with an ordinary Git project containing an evaluation script and a small
`launch.yml` manifest. Hiraku then gives the project a research structure:
one measurable Goal, one reference Experiment, and one explicit improvement
attempt.

## Workflow

```bash
# 1. Initialize Hiraku once in the project and commit the project setup.
hiraku init
git add .
git commit -m "initialize project"

# 2. State what should improve and how it will be measured. The manifest
#    names the existing evaluation command; Hiraku does not invent the model
#    or evaluation script for the user.
hiraku goal create iris-accuracy \
  --metric accuracy \
  --direction max \
  --launch launch.yml


# J: hiraku task create iris-accuracy --goal "Improve iris accuracy" --launch launch.yml --metric accuracy --direction max
# J: hiraku task set iris-accuracy  # (define active task, automatic after task create)

# 3. Record and run a simple reference implementation first.
hiraku experiment create iris-accuracy/baseline \
  --kind baseline \
  --hypothesis "The existing classifier is a useful reference."
hiraku launch iris-accuracy/baseline:evaluate

# J: hiraku experiment set baseline    # (define active experiment, automatic after create)
# J: hiraku launch evaluate [--task iris-accuracy]  #  task launcher

# 4. Ask the repository-local /hiraku skill, or make a small change yourself,
#    and describe the claim being tested. Creating the Experiment also checks
#    out its Git branch so no manual branch command is needed.
hiraku experiment create iris-accuracy/scaled-features \
  --hypothesis "Standardizing the input features improves accuracy."
# implement the change, then commit it on the checked-out Experiment branch
git add .
git commit -m "test standardized features"
hiraku launch iris-accuracy/scaled-features:evaluate

# 5. Compare evidence and record the decision. The TUI is an optional compact
#    view for users who prefer browsing over command output.
hiraku runs iris-accuracy --sort accuracy --descending
hiraku experiment tree iris-accuracy
hiraku experiment conclude iris-accuracy/scaled-features --verdict supports
hiraku tui

# J: hiraku experiment update [scaled-features] --verdict supports --status complete
```

The beginner only needs to provide the objective, metric, evaluation command,
and source change. Every launch is recorded before execution, including failed
runs, and each completed run is tied to the exact committed source. A second
attempt can be created as another sibling Experiment; parent Experiments,
reports, and the `hiraku auto` workflow can be introduced later.

## Preferred user interfaces

The root CLI and repository-local `/hiraku` skill provide a guided,
approval-aware workflow. `hiraku runs` and `hiraku experiment tree` answer what
was tested and what evidence was collected; `hiraku tui` is an optional compact
review surface. All three use the same lifecycle records, so the beginner can
start with terminal commands and move to the TUI without changing the workflow.
