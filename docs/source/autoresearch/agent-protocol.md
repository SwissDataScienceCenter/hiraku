# Runtime-neutral agent protocol

Hiraku does not invoke a named model provider or assume a particular agent CLI. The core supports
two equivalent integration styles.

## Skill-driven integration

An agent already running in the repository reads `.agents/skills/hiraku/SKILL.md` and drives the
loop with `hiraku auto` commands. This requires no nested agent process and works wherever the agent
can read repository instructions, edit source, and execute the CLI.

The lifecycle boundary remains strict:

```text
agent decides and edits source
        ↓
Hiraku validates, commits, launches, computes verdicts, and writes SQLite
```

The agent must never edit `.hiraku/state.db` or generated reports.

## Unattended adapter integration

`hiraku auto loop --adapter <executable>` starts an executable adapter for each `propose`,
`implement`, and `analyze` action. Hiraku sends one JSON object on stdin. The adapter returns exactly
one JSON object on stdout. Diagnostic output belongs on stderr.

The adapter may use any local or remote agent runtime. Hiraku neither knows nor records the vendor
command used behind the adapter.

Request envelope:

```json
{
  "request_id": "A-...",
  "protocol": "hiraku.agent",
  "version": 1,
  "action": "propose",
  "project_root": "/absolute/project/path",
  "state": {},
  "instructions": {}
}
```

Response envelope:

```json
{
  "request_id": "A-...",
  "protocol": "hiraku.agent",
  "version": 1,
  "action": "propose",
  "payload": {}
}
```

The response must echo the request ID, protocol version, and action. Hiraku rejects malformed,
unbound, timed-out, or non-zero adapter responses.

### `propose`

```json
{
  "decision": "propose",
  "experiment_id": "class-weighted-rf",
  "hypothesis": "Class weighting improves the Goal metric.",
  "parent_experiment_id": null
}
```

The adapter may instead return `{"decision": "stop", "reason": "..."}`.

### `implement`

The adapter runs in the active Experiment branch and may edit project source. It must not commit or
write lifecycle state. On success:

```json
{"status": "implemented", "summary": "Added class weighting to model configuration."}
```

Hiraku then validates and commits the changes.

### `analyze`

Hiraku includes the computed primary-metric verdict in the instructions. The adapter explains the
evidence and decides whether another bounded iteration is useful:

```json
{
  "verdict": "supports",
  "rationale": "The candidate exceeded the immutable baseline on the recorded metric.",
  "continue": true
}
```

The adapter cannot override Hiraku's computed verdict. Every request and response is recorded as an
event without storing private model reasoning.

### `draft_goal`

Use the same adapter contract to draft a Goal from bounded repository context. The draft is advisory
and is not written to SQLite unless the human explicitly passes `--apply`:

```bash
hiraku goal draft heart-disease \
  --question "Define a measurable objective for improving the classifier." \
  --adapter ./agent-adapter
```

The payload contains `title`, `objective`, `metric`, `direction`, `target`, `evaluation_protocol`,
`constraints`, `unset`, and `rationale`. Applying a reviewed draft requires a launch manifest:

```bash
hiraku goal draft heart-disease \
  --question "Define a measurable objective for improving the classifier." \
  --adapter ./agent-adapter --launch launch.yml --apply
```

Context contains an allowlisted file inventory and truncated text, with secrets, local state, virtual
environments, and Git internals excluded. Use `--max-files` and `--max-bytes` to tune the bounds.
