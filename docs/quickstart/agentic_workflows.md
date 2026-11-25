# Agentic workflows

Start with a coding-agent conversation to explore a research question and
direct each next step. For repeatable procedures, use a LangGraph workflow to
define the steps, review points, and state to resume later.

Use the project from [First experiment](first_experiment.md). It already has a
`binary-classification` task, a threshold baseline, and a `local` leaderboard.

## Connect your coding agent

`hiraku init` installs the Hiraku skill and configures the MCP server
for Claude Code, Codex, and Pi. The skill tells the agent how to work with
Hiraku. The MCP server gives it tools to read records, query runs, and advance
workflows.

Open the agent in that directory and follow its prompts to trust the project or 
enable the server. Ask the agent:

> Use Hiraku's MCP tools to list the project records.

Look for `binary-classification` and its baseline experiment. If the tools are
missing, see [Connect a coding agent](../guides/mcp.md) for client configuration
and PATH setup.

## User-driven development
   
Set the research question and direct the agent from your coding harness. Ask it
to inspect the project and propose a next step before it edits anything:

> Read the `binary-classification` task and its baseline runs on the `local`
> leaderboard. Propose a test of whether raising the threshold from 0.5 to
> 0.6 preserves accuracy. Wait for my feedback before editing `evaluate.py`.

If you want to proceed, ask the agent to implement the change and record the
experiment:

> Apply the threshold change and show me the diff. When I approve it, commit
> the change and create a child experiment record under `binary-classification`
> with the hypothesis and commit. Submit `uv run python evaluate.py --seed 0`
> for that record. Once the job finishes, compare the tracked result with the
> seed 0 baseline and summarize what you observed.

The agent can use Hiraku's record, job, and run tools directly. Your conversation
sets the pace and provides the review points without starting a workflow session.

## Start an agent workflow

To try the same research question with defined steps, start from the original
baseline project. `hiraku init` installs the `collaborative` workflow. Ask the
agent to list available workflows and look for `collaborative`. Use a client
that supports MCP elicitation for its structured approval prompts.

Give the agent a question it can answer by evaluating one change:

> Use Hiraku MCP to start the `collaborative` workflow with the arguments below.
> Read the task and its baseline runs. Propose testing whether raising the
> classification threshold from 0.5 to 0.6 preserves validation accuracy.
> Present the proposal before editing code and use the workflow's approval
> prompts as we go.
> 
> ```json
> {
>   "task": "binary-classification",
>   "leaderboard": "local",
>   "command": ["uv", "run", "python", "evaluate.py", "--seed", "0"],
>   "rounds": 1
> }
> ```

The workflow follows a four predefined steps:
```
Draft -> Implement -> Launch -> Analyze
```
Explore the files in `hiraku/workflows/collaborative/` to see how the workflow is defined. 
See [Workflows](../guides/workflows.md) for an in-depth guide.

