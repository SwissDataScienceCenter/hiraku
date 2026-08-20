# Heart Disease guided experiment

This example compares logistic regression and a random forest on the processed Cleveland cohort
from the UCI Heart Disease dataset. Both models use identical deterministic stratified five-fold
splits. Missing-value handling and preprocessing are fitted inside each training fold.

Install the optional modeling dependencies and work in a clean Git repository containing this
directory:

```bash
uv sync --extra heart-demo
hiraku init
git add . && git commit -m "initialize hiraku"
hiraku exp create heart-disease-models
cp examples/heart-disease/launch.yml experiments/heart-disease-models/launch.yml
git add . && git commit -m "configure heart disease experiment"
hiraku launch heart-disease-models:logistic
hiraku launch heart-disease-models:random-forest
hiraku runs heart-disease-models --sort roc_auc_mean --descending
```

Each entrypoint verifies the dataset checksum and structure before fitting. It records ROC AUC and
accuracy means and standard deviations. Improving a metric is not required for the Hiraku PoC.

See [DATASET.md](DATASET.md) for provenance, attribution, license, and the medical-use disclaimer.

