---
id: count-steps
description: Check that the harness follows numbered instructions and gates.
---
# Count steps

This workflow checks that your harness follows Hiraku instructions exactly. It
does no research work and changes no files. Each step carries a random token;
reply with the token of the current step, never one from an earlier step.

## Step

Print the step line from the context to the user, for example
`Step 3/10: token 1a2b3c4d`. Then reply `ack` with the step's `token`.

## Gate

Stop here and show the user your plan for the remaining steps: how many are
left and that you will acknowledge each one in order. Ask whether to continue.
Reply `proceed` with the step's `token` only if the user agrees, or `finish` to
stop.
