# Create or refine an experiment

1. Inspect `.hiraku.yml`, existing experiments, and relevant project code before asking questions.
2. Establish the scope, evaluation protocol, mode, branch, and success criteria with the user.
3. Run `hiraku exp create <id> --mode <guided|auto>` only after the repository is clean.
4. Refine `experiment.md`, `policy.md`, and `launch.yml` directly.
5. In guided mode, show the resulting scope and plan and obtain approval before implementation.
6. In auto mode, make every policy bound concrete and obtain explicit conversational approval
   before starting the loop.

