---
id: eurosat-autoresearch
summary: Bounded architecture experiments for EuroSAT RGB under a fixed CPU protocol.
---

# EuroSAT RGB Autoresearch

Use the selected Hiraku task as the research contract. The baseline and each
follow-up experiment are child records of that task.

## Protocol

Keep every experiment comparable with the flattened-image MLP baseline:

- EuroSAT RGB at 64×64 resolution,
- stratified 80/20 train/validation split,
- seed 42,
- Adam with learning rate 0.001,
- batch size 64,
- 10 epochs on CPU,
- no pretrained models.

Use `val/accuracy` as the primary metric and `val/loss` as supporting evidence.
Do not change the protocol during this loop.

## Bounded experiment loop

Run at most five experiments total, including the MLP baseline. Start with the
literature review, establish the baseline, then use its results and the review
to select one focused architectural experiment at a time.

For each follow-up:

1. State a falsifiable hypothesis and a minimal architectural change.
2. Create one experiment record under the task.
3. Implement and commit the change.
4. Launch it through Hiraku and inspect its attributed W&B runs.
5. Record the conclusion against both declared metrics.
6. Continue only if another evidence-backed experiment fits the remaining
   budget and the fixed protocol.

Stop at the five-experiment limit, when no justified experiment remains, or
when safe progress requires a change to the research question, metrics,
protocol, or authorized scope. Report all experiment records, run results, and
the final conclusion.
