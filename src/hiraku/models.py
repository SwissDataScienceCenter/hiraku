from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import asdict, dataclass
from typing import Any


@dataclass(frozen=True)
class Goal:
    id: str
    title: str
    objective: str
    metric: str
    direction: str
    target: float | None
    evaluation_protocol: str
    constraints: list[str]
    baseline_commit: str
    status: str
    launch_manifest: dict[str, Any]
    created_at: str
    updated_at: str

    @classmethod
    def from_row(cls, row: Mapping[str, Any]) -> Goal:
        data = dict(row)
        data["constraints"] = json.loads(data.pop("constraints_json"))
        data["launch_manifest"] = json.loads(data.pop("launch_manifest_json"))
        return cls(**data)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class Experiment:
    id: str
    goal_id: str
    parent_id: str | None
    kind: str
    hypothesis: str
    branch: str
    parent_commit: str
    evaluated_commit: str | None
    status: str
    verdict: str | None
    origin: str
    created_at: str
    updated_at: str

    @classmethod
    def from_row(cls, row: Mapping[str, Any]) -> Experiment:
        return cls(**dict(row))

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)
