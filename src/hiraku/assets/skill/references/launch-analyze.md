# Launch and analyze runs

1. Read the experiment evaluation protocol and policy before launching anything.
2. In guided mode, obtain explicit approval for the named entrypoint launch.
3. Ensure all intended code and documentation are committed; `hiraku launch` requires a clean
   worktree and the recorded experiment branch.
4. Run `hiraku launch <experiment>:<entrypoint>`.
5. Inspect results with `hiraku runs <experiment>` or JSON output. When ranking, specify the metric
   and direction explicitly.
6. Check missing/non-numeric values and compare runs only under the approved evaluation protocol.
7. In guided mode, obtain approval before writing findings, conclusions, or next-step decisions.

