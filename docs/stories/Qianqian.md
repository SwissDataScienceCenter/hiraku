# Qianqian: Governed, evidence-driven parallel research

The main goal of the workflow is to turn a research question/task into an auto iterative loop of experiments without surrendering scientific control to the agent. The evaluation protocol is fixed up front; agents may discover and design proposals of experiments, but only a person can approve them for execution. Approved experiments run reproducibly and in parallel, results are explicitly triaged, and promotion to the leaderboard requires protocol compliance, reproduction, and sign-off. The final report is assembled only from recorded evidence. 

## Workflow

```bash
# 1. Configure the research bounds and draft a fixed evaluation protocol.
$EDITOR research/config.md
hiraku-auto context
hiraku-auto goal draft dinov3-crossdomain \
  --question "Would DINOv3 improve reprojection between Mapillary and Natix frames?"
hiraku-auto goal show dinov3-crossdomain
$EDITOR research/goals/dinov3-crossdomain.md  # Resolve every [UNSET: ...].
hiraku-auto check

# J: No equivalent for context yet - we can make it a first-class entity
# hiraku context create literature-search
# or with agent: hiraku-auto context create literature-search

### Q: In my original version, the 'goal' step can defines the questions, datasets, and evaluation protocol based on optionally with the context of the existing project. 
# that is:
# hiraku-auto context [-add <path>] # optional, to provide context of the existing project
# hiraku-auto goal draft <goal-id> --question <question> --context <path (directly, no need to call 'hiraku-auto context' in advance)>
### all functional commands can be found in 'docs/autoresearch/commands.md'

# currently, the agent won't search for the literature context.
# Good idea :) I see the point. With the websearched-context, the agent can better draft the overall evaluation protocol.

# J: Deterministic task creation
# J: hiraku task create dinov3-crossdomain --question "Would DINOv3 improve reprojection between Mapillary and Natix frames?" --context literature-search
# J: dinov3-crossdomain becomes the "active" task, or set with
# J: hiraku task set dinov3-crossdomain
# J: hiraku task show [dinov3-crossdomain] 
# J: hiraku-auto task check [dinov3-crossdomain]

### Q: is this the alignment of naming? meaning we change 'hiraku-auto goal draft' to 'hiraku task create'? 
# I'm fine with changing 'hiraku-auto goal' to 'hiraku task'
# but I prefer 'draft' here, because there is also 'hiraku-auto goal add' to directly write a goal without agent drafting.



# 2. Optionally discover hypotheses or design an experiment tree.
# Agent output remains a proposal and cannot be launched.
hiraku-auto agent discover dinov3-crossdomain
hiraku-auto agent design dinov3-crossdomain
hiraku-auto proposal list
# J: I am assuming that discover is the proposal step (or another context discovery step?). Either way:
# J: hiraku context create dinov3-crossdomain
# J: hiraku-auto context discover dinov3-crossdomain
# J: hiraku-auto experiment propose "Propose 5 experiments for dinov3-crossdomain" --context dinov3-crossdomain --context literature-search
# J: hiraku experiment list --proposal
# J: Or, if we want to make "proposal" a first-class action/shorthand
# J: hiraku proposal list
# J: At this point we have 5 "experiments" in proposal state, they are not implemented yet

# Q：'hiraku-auto agent discover ... --context ' will generate proposals, maybe no need to run 'hiraku-auto experiment propose' again.

# 3. Accept or decline each proposal. Keep declined ideas and their reasons.
hiraku-auto proposal accept feature-whitening
hiraku-auto proposal decline dinov3-layer-choice \
  --reason "Not worth four runs until the backbone swap shows a direction."
hiraku-auto tree

# J: We could keep the "accept" step as a first-class, semantic action, or we just set a flag
# J: "accept" action == update + implement?
# J: hiraku-auto proposal accept feature-whitening

# J: Isolated CLI update, without implementation
# J: hiraku experiment update feature-whitening --status accepted 
# J: hiraku proposal update feature-whitening --status accepted  # (proposal == experiment shorthand)

### Q: I have implemented 'agent implement' to generate the code for the certain accepted proposal; but we can also directly run: 
# hiraku-auto proposal accept feature-whitening --implement (with this flag, the agent will implement the proposal and generate the code for it)
# if without '--implement', the agent will only accept the proposal and update the status to 'accepted' without generating code for it.

# 4. Run approved, commit-backed experiments in parallel.
hiraku-auto loop dinov3-crossdomain \
  --launch reconstruct \
  --max-parallel 4 \
  --gpus 0,1,2,3 \
  --seed 1
hiraku-auto loop dinov3-crossdomain \
  --launch reconstruct \
  --max-parallel 4 \
  --gpus 0,1,2,3 \
  --seed 2
# J: I am not exactly sure what happens here. Are we just launching? Or are we running an implementation / experiment loop?
# J: loop could be a program that defines the constraints. We use an agent to launch
# J: hiraku program create loop --launch reconstruct --max-parallel 4 --gpus 0,1,2,3 --seed 1
# J: hiraku-auto task launch --program loop  # here invoking a task/launch action?

# 5. Triage the evidence and review any proposed follow-up.
hiraku-auto agent triage dinov3-swap
$EDITOR experiments/dinov3-swap/experiment.md
hiraku-auto proposal list
hiraku-auto tree

# J: hiraku-auto experiment triage [dinov3-swap]  # invoking task/triage action
# J: hiraku experiment update dinov3-swap --status triaged  # invoking task/update action
# J: hiraku experiment tree 
# J: hiraku experiment list 

# 6. Promote only results that pass every evidence gate and human sign-off.
hiraku-auto promote dinov3-swap \
  --run <candidate-run-id> \
  --signed-off-by "Qianqian Zou"
# J: hiraku experiment update dinov3-swap --signed-off-by "Qianqian Zou" --status promoted --run <candidate-run-id>

# 7. Build an evidence-only report and inspect the complete research tree.
hiraku-auto report dinov3-crossdomain
hiraku-auto dash -o tree.html
hiraku-auto ui
hiraku-auto check

# J: hiraku-auto task report dinov3-crossdomain  # invoking task/report action (updating task.md or another specified file)
# J: hiraku dashboard dinov3-crossdomain -o tree.html  # invoking task/dashboard action
# J: hiraku dashboard dinov3-crossdomain  # direct ui invocation
# J: hiraku-auto task check [dinov3-crossdomain]  # invoking task/check action
```

## Preferred user interfaces

The command-line interface is the primary surface for defining goals, reviewing proposals, launching parallel work, triaging evidence, and promoting results. Research records remain readable and directly editable Markdown files. A generated tree dashboard and interactive UI provide complementary, clickable views for inspecting experiment lineage, runs, findings, and gaps in the record.
