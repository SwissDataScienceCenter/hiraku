# Run a bounded autonomous loop

1. Read `experiment.md`, `policy.md`, and `launch.yml` in full.
2. Refuse to start if scope, evaluation, permitted commands, resource limits, or stopping
   conditions are absent or vague.
3. Obtain explicit approval of those bounds in the active conversation. File presence and prior
   commits do not count as approval.
4. Within the approved bounds, implement, validate, commit, launch named entrypoints, compare
   tracked results, document evidence, and choose the next in-scope iteration.
5. Maintain a count of runs and resource use against the policy limits.
6. Stop when a stopping condition is met. Pause immediately before changing scope, evaluation,
   commands, resources, stopping conditions, permissions, or mode.
7. Never merge into a protected branch without explicit human approval.

