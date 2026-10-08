"""Validated input models for Hiraku MCP operations."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from ..core.record import Metric


class ProposedEntrypoint(BaseModel):
    name: str = Field(min_length=1)
    command: list[str] = Field(min_length=1)
    args: dict[str, Any] = Field(default_factory=dict)


class ExperimentProposal(BaseModel):
    id: str = Field(min_length=1)
    parent_id: str
    summary: str = Field(min_length=1)
    hypothesis: str = Field(min_length=1)
    implementation_plan: str = Field(min_length=1)
    evaluation_plan: str = Field(min_length=1)
    entrypoint: ProposedEntrypoint | None = None


class ResearchTask(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str = Field(min_length=1)
    goal: str = Field(min_length=1)
    metrics: list[Metric] = Field(min_length=1)
    protocol: str = Field(min_length=1)
    tracking_backend: dict[str, Any] | None = None


class Analysis(BaseModel):
    run_ids: list[str] = Field(min_length=1)
    conclusion: str = Field(min_length=1)
    decision: Literal["keep", "discard", "inconclusive"]


class LiteratureSource(BaseModel):
    title: str = Field(min_length=1)
    identifier: str = ""
    url: str = ""
    authors: list[str] = Field(default_factory=list)
    year: int | None = None
    verification_status: Literal[
        "discovery_summary", "abstract_reviewed", "full_text_reviewed", "code_reviewed"
    ]
    evidence_notes: str = ""


class LiteratureReview(BaseModel):
    context_id: str = Field(min_length=1)
    scope: str = Field(min_length=1)
    synthesis: str = Field(min_length=1)
    candidate_directions: list[str] = Field(default_factory=list)
    limitations: str = Field(min_length=1)
    sources: list[LiteratureSource] = Field(min_length=1)
