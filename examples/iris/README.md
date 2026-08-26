# Iris example

This is a generic, self-contained example using scikit-learn’s built-in Iris dataset. It needs no
dataset download. Run these commands from the repository root:

```bash
uv sync --extra dev --extra tui --extra heart-demo
git add .
git commit -m "install unified Hiraku core"
hiraku init
git add .hiraku.yml .hiraku/.gitignore .agents
git commit -m "initialize hiraku"
hiraku goal create iris --metric accuracy_mean --direction max --launch examples/iris/launch.yml --objective "Improve Iris classification accuracy."
hiraku auto loop iris --max-experiments 2 --max-runs 2 --time-budget 5m
hiraku runs iris --sort accuracy_mean --descending
hiraku experiment tree iris
hiraku report generate iris
hiraku tui
```

The loop automatically evaluates a majority-class baseline, creates an Experiment from a proposal,
commits a logistic-regression implementation, evaluates it, and records a `supports` verdict.

For the guided equivalent:

```bash
hiraku experiment create iris/baseline --kind baseline --hypothesis "The majority classifier establishes the reference."
hiraku launch iris/baseline:evaluate
hiraku experiment create iris/logistic --hypothesis "Logistic regression improves accuracy over the baseline."
cd examples/iris
# implement the model in the Experiment branch, then commit it
cd ../..
hiraku launch iris/logistic:evaluate
hiraku runs iris --sort accuracy_mean --descending
hiraku experiment conclude iris/logistic --verdict supports
```

