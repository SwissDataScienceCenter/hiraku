---
id: count-steps
description: Check that the harness follows numbered instructions and stops for the user.
---
# Count steps

## Step

Print the step line from the context to the user, for example
`Step 3/5: token 1a2b3c4d`. Then reply `ack` with the step's `token`.

## Next

Tell the user which step is done and how many remain, and ask whether to run
the next step. Reply `step` only if the user agrees, or `finish` to stop.
