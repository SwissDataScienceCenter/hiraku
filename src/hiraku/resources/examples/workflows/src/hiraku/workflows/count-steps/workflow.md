---
id: count-steps
description: Count numbered steps with a LangGraph handoff to the current harness.
arguments:
  count: 5
---
# Count steps

## Step

Print this line to the user exactly: `Step {{step}}/{{count}}: token {{token}}`.
Then ask whether to continue. Wait for the user's answer before calling
`workflow_resume` with workflow session ID `{{workflow_session_id}}` and response
`{"token": "{{token}}", "decision": "continue"}` if they agree, or
`{"token": "{{token}}", "decision": "finish"}` if they want to stop.

## Next

Tell the user which step is done and how many remain. Do not call
`workflow_resume` until the user answers.

## Done

Tell the user: `Finished after {{steps}} of {{count}} steps.`
