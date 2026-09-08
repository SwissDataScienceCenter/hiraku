# Troubleshooting

| Symptom | What to check |
| --- | --- |
| `hiraku: command not found` | Install the CLI with `uv tool install .` from the Hiraku checkout. Run `uv tool update-shell` and reopen the shell if needed. |
| No `hiraku.yml` found | Run from the research project, or initialize it once with `hiraku init`. |
| Experiment or entrypoint not found | Inspect `hiraku status`, `hiraku task show`, and `hiraku experiment show EXPERIMENT_ID`. Confirm the active task. |
| Training dependency missing | Install dependencies in the environment used by the entrypoint. The Hiraku tool environment is separate from the training environment. |
| Source changes do not affect the result | A bound experiment runs its recorded commit. Commit changes and update the binding or create a new experiment. |
| Launch succeeds but no runs appear | Check that the script logs through the chosen backend and includes matching task/experiment attribution. |
| W&B or MLflow import error during inspection | Install that extra in the Hiraku CLI environment as well as the SDK in the training environment. |
| Agent skill not discovered | Give the agent the explicit path `.agents/skills/hiraku/SKILL.md`. |

Use `hiraku --help` and the [CLI reference](../api/cli.md) for syntax.
A dirty checkout alone is not rejected by the current launcher; committed
launches evaluate their isolated source snapshot.
