# Heart-disease-style classification example

This deterministic demo uses a bundled scikit-learn binary-classification dataset so it needs no
network access. Run each command from the repository root:

```bash
uv sync --extra heart-demo --extra tui
git add .
git commit -m "install unified Hiraku core"
hiraku init
git add .hiraku.yml .hiraku/.gitignore .agents
git commit -m "initialize hiraku"
hiraku goal create heart-disease --metric roc_auc_mean --direction max --launch examples/heart-disease/launch.yml --objective "Improve cross-validated binary-classification ROC AUC."
hiraku experiment create heart-disease/logistic --kind baseline --hypothesis "Logistic regression establishes the reference model."
hiraku launch heart-disease/logistic:evaluate
hiraku launch heart-disease/logistic:evaluate
hiraku experiment create heart-disease/random-forest --hypothesis "Random forest improves ROC AUC."
hiraku launch heart-disease/random-forest:evaluate
hiraku launch heart-disease/random-forest:evaluate
hiraku runs heart-disease --sort roc_auc_mean --descending
hiraku experiment conclude heart-disease/random-forest --verdict refutes
hiraku experiment tree heart-disease
hiraku report generate heart-disease
hiraku tui
```

There is no `git switch` between sibling Experiments: `experiment create` checks out a new branch
from the Goal's immutable baseline automatically. The Goal and Experiment records remain available
because they live in ignored, branch-independent `.hiraku/state.db`.
