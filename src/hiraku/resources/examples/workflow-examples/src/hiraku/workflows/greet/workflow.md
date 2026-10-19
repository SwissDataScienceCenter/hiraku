---
id: greet
description: Greet someone a few times; a prompt-only workflow with arguments.
arguments:
  name: {required: true, description: Who to greet}
  times: {type: integer, default: 2, description: How many greetings to print}
  formal: {type: boolean, default: false, description: Whether to greet formally}
---
# Greet

Print {{times}} greetings to {{name}}, one per line. Greet formally:
{{formal}}. Do not change any files.
