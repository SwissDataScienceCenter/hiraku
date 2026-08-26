#!/usr/bin/env python3
"""Deterministic reference adapter for exercising the agent protocol.

This is not an AI implementation. Replace its decision functions behind the same
stdin/stdout contract when connecting any agent runtime.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any


def payload(request: dict[str, Any]) -> dict[str, Any]:
    action = request["action"]
    if action == "propose":
        return {
            "decision": "propose",
            "experiment_id": "protocol-candidate",
            "hypothesis": "The bounded alternative improves score over the immutable baseline.",
            "parent_experiment_id": None,
        }
    if action == "implement":
        Path(request["project_root"], "experiment-config.json").write_text(
            json.dumps({"strategy": "agent-protocol"}, indent=2) + "\n",
            encoding="utf-8",
        )
        return {"status": "implemented", "summary": "Selected the protocol candidate strategy."}
    return {
        "verdict": request["instructions"]["computed_verdict"],
        "rationale": "The recorded candidate score exceeds the immutable baseline score.",
        "continue": False,
    }


def main() -> None:
    request = json.load(sys.stdin)
    json.dump(
        {
            "request_id": request["request_id"],
            "protocol": request["protocol"],
            "version": request["version"],
            "action": request["action"],
            "payload": payload(request),
        },
        sys.stdout,
    )


if __name__ == "__main__":
    main()
