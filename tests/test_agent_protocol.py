from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

from hiraku.agent import (
    PROTOCOL_NAME,
    PROTOCOL_VERSION,
    AgentRequest,
    ProcessAgentAdapter,
    response_payload,
)
from hiraku.errors import HirakuError


def request(root: Path) -> AgentRequest:
    return AgentRequest.create("propose", root, {"goal": {"id": "demo"}}, {"task": "propose"})


def test_process_adapter_is_runtime_neutral_json_over_stdio(tmp_path: Path):
    script = tmp_path / "adapter.py"
    script.write_text(
        """import json, sys
request = json.load(sys.stdin)
json.dump({
    "request_id": request["request_id"],
    "protocol": request["protocol"],
    "version": request["version"],
    "action": request["action"],
    "payload": {"decision": "stop", "reason": "enough evidence"},
}, sys.stdout)
""",
        encoding="utf-8",
    )
    value = request(tmp_path)
    response = ProcessAgentAdapter([sys.executable, str(script)]).exchange(value)
    assert response_payload(response, "propose") == {
        "decision": "stop",
        "reason": "enough evidence",
    }


def test_process_adapter_rejects_unbound_response(tmp_path: Path):
    script = tmp_path / "adapter.py"
    script.write_text(
        "import json, sys; json.dump({"
        f"'request_id': 'wrong', 'protocol': '{PROTOCOL_NAME}', 'version': {PROTOCOL_VERSION}, "
        "'action': 'propose', 'payload': {}}, sys.stdout)\n",
        encoding="utf-8",
    )
    with pytest.raises(HirakuError, match="wrong or missing request_id"):
        ProcessAgentAdapter([sys.executable, str(script)]).exchange(request(tmp_path))


def test_process_adapter_rejects_non_json(tmp_path: Path):
    script = tmp_path / "adapter.py"
    script.write_text("print('not json')\n", encoding="utf-8")
    with pytest.raises(HirakuError, match="exactly one JSON object"):
        ProcessAgentAdapter([sys.executable, str(script)]).exchange(request(tmp_path))


def test_request_contract_is_serializable_and_versioned(tmp_path: Path):
    value = request(tmp_path).to_dict()
    assert value["protocol"] == PROTOCOL_NAME
    assert value["version"] == PROTOCOL_VERSION
    assert json.loads(json.dumps(value))["state"]["goal"]["id"] == "demo"
