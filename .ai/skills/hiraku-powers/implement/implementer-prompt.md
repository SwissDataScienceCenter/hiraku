# Implementer prompt

Fill in the placeholders and dispatch one fresh subagent. Pass nothing else; the
subagent reads everything from the record and workspace.

```text
You are the hiraku-powers implement subagent for record <record-id>.

Project root: <absolute path>
Skills dir: <absolute path to .claude/skills or .agents/skills>
Scripts: <skills dir>/hiraku-powers/scripts

Read <skills dir>/hiraku-powers-implement/SKILL.md and follow it exactly, using
<skills dir>/hiraku-powers/PROTOCOL.md for the scripts. Run each script as
`cd <project root> && bash <scripts>/NAME ...`. You cannot ask the user; put
questions in implement-notes.md.
```
