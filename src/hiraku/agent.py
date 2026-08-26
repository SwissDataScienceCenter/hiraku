from __future__ import annotations

import json
import os
import subprocess
import uuid
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Protocol

from .errors import HirakuError

PROTOCOL_NAME = "hiraku.agent"
PROTOCOL_VERSION = 1


@dataclass(frozen=True)
class AgentRequest:
    """One runtime-neutral request sent to a research agent adapter."""

    request_id: str
    protocol: str
    version: int
    action: str
    project_root: str
    state: dict[str, Any]
    instructions: dict[str, Any]

    @classmethod
    def create(
        cls,
        action: str,
        project_root: Path,
        state: dict[str, Any],
        instructions: dict[str, Any],
    ) -> AgentRequest:
        return cls(
            request_id=f"A-{uuid.uuid4().hex[:12]}",
            protocol=PROTOCOL_NAME,
            version=PROTOCOL_VERSION,
            action=action,
            project_root=str(project_root.resolve()),
            state=state,
            instructions=instructions,
        )

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class AgentAdapter(Protocol):
    def exchange(self, request: AgentRequest) -> dict[str, Any]: ...


class ProcessAgentAdapter:
    """Transport JSON over stdin/stdout to any executable adapter.

    Hiraku deliberately knows nothing about the model, vendor, SDK, or interactive
    agent behind the executable. The adapter receives one JSON request on stdin and
    must return exactly one JSON object on stdout.
    """

    def __init__(self, argv: list[str], *, timeout: int = 900):
        if not argv or not argv[0]:
            raise HirakuError("Agent adapter command cannot be empty.")
        if timeout < 1:
            raise HirakuError("Agent adapter timeout must be positive.")
        self.argv = argv
        self.timeout = timeout

    def exchange(self, request: AgentRequest) -> dict[str, Any]:
        env = os.environ.copy()
        env.update(
            {
                "HIRAKU_AGENT_PROTOCOL": PROTOCOL_NAME,
                "HIRAKU_AGENT_PROTOCOL_VERSION": str(PROTOCOL_VERSION),
                "HIRAKU_AGENT_REQUEST_ID": request.request_id,
                "HIRAKU_AGENT_ACTION": request.action,
            }
        )
        try:
            completed = subprocess.run(
                self.argv,
                cwd=request.project_root,
                env=env,
                input=json.dumps(request.to_dict()),
                capture_output=True,
                text=True,
                timeout=self.timeout,
                check=False,
            )
        except FileNotFoundError as exc:
            raise HirakuError(f"Agent adapter executable was not found: {self.argv[0]}") from exc
        except subprocess.TimeoutExpired as exc:
            raise HirakuError(f"Agent adapter timed out after {self.timeout} seconds.") from exc
        except OSError as exc:
            raise HirakuError(f"Could not start agent adapter: {exc}") from exc
        if completed.returncode:
            detail = (completed.stderr or completed.stdout).strip()[:500]
            raise HirakuError(
                f"Agent adapter exited with status {completed.returncode}" + (f": {detail}" if detail else ".")
            )
        try:
            response = json.loads(completed.stdout)
        except json.JSONDecodeError as exc:
            raise HirakuError("Agent adapter must return exactly one JSON object on stdout.") from exc
        if not isinstance(response, dict):
            raise HirakuError("Agent adapter response must be a JSON object.")
        if response.get("request_id") != request.request_id:
            raise HirakuError("Agent adapter response has the wrong or missing request_id.")
        if response.get("protocol") != PROTOCOL_NAME or response.get("version") != PROTOCOL_VERSION:
            raise HirakuError("Agent adapter response uses an unsupported protocol or version.")
        return response


def response_payload(response: dict[str, Any], expected_action: str) -> dict[str, Any]:
    if response.get("action") != expected_action:
        raise HirakuError(f"Agent adapter response action must be '{expected_action}'.")
    payload = response.get("payload")
    if not isinstance(payload, dict):
        raise HirakuError("Agent adapter response must contain an object payload.")
    return payload
