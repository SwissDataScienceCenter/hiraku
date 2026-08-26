# Bounded autonomous heart-disease research

Do not start until the human approves an iteration limit or wall-clock limit below. The goal,
dataset checksum, split, preprocessing, metrics, and evaluation protocol in `task.md` are fixed.

## Approved boundaries

- Editable files: training implementation and model configuration only.
- Permitted commands: Hiraku commands, Git commands needed by this workflow, and the declared
  `logistic` and `random-forest` launches.
- Dependencies: existing dependencies only; do not add or upgrade packages.
- Compute: local CPU only, one run at a time.
- Iteration limit or wall-clock limit:
- Git permissions (commit, branch, integrate):

After approval, establish a baseline and repeat within the bound:

1. inspect the task, prior experiments, entrypoints, and `hiraku runs <task> --json`;
2. choose one model or configuration hypothesis and create a matching experiment whose invocation is
   exactly `logistic` or `random-forest`;
3. implement one coherent in-scope change, commit it, and bind its exact commit;
4. launch the declared entrypoint and compare `val/log_loss` first, then `val/accuracy`;
5. diagnose crashed runs separately from rejected scientific hypotheses, and record status, decision,
   and conclusion in the experiment; and
6. stop at the approved bound or continue with the next hypothesis.

At completion, synthesize the evidence and decisions in `task.md` for human review. Never edit the
run table or MLflow data directly, alter the fixed protocol, add dependencies, push, or merge without
the recorded authority.
