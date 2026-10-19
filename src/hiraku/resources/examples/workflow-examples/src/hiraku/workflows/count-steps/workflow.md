---
id: count-steps
description: Check that the harness follows numbered instructions and stops for the user.
---
# Count steps

## Step

Print this line to the user exactly: `Step {{step}}/{{count}}: token {{token}}`.
Then reply `ack` with the token {{token}}.

## Next

Tell the user which step is done and how many remain, and ask whether to run
the next step. Reply `step` only if the user agrees, or `finish` to stop.

## Done

Tell the user how many of the steps in the context ran.
