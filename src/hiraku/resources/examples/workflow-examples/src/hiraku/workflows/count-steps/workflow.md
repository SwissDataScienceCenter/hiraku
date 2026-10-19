---
id: count-steps
description: Check that the harness follows numbered instructions and that the user approves each step.
---
# Count steps

## Step

Print this line to the user exactly: `Step {{step}}/{{count}}: token {{token}}`.
Then send the event `ack` with the token {{token}}; the user approves it before
the next step runs. If the user asks to stop instead, send `finish`.

## Done

Tell the user how many of the steps in the context were acknowledged.
