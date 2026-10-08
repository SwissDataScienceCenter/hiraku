"""The shape of each workflow's JSON reply, checked before anything is written.

Each `prompts/<workflow>.md` ends in an "Output contract". The fields the code acts on
(ids, parents, verdicts, the commit, the triage decision) are typed here; the prose
fields (findings, rationale, provenance, ...) stay free text and are not checked. A
reply that breaks its contract is refused as a whole with an `AgentError` naming every
problem, rather than half-applied or, worse, silently ignored — a misspelt verdict used
to be dropped without a word.

Unknown keys are allowed: a prompt may ask for more than the code reads, and a project
may override a prompt (`.agents/skills/hiraku-agent/`) to ask for more still.
"""

from __future__ import annotations

from typing import Any, ClassVar, Literal

from pydantic import (
    BaseModel,
    ConfigDict,
    ValidationError,
    field_validator,
    model_validator,
)

from .errors import AgentError

Verdict = Literal["supports", "refutes", "inconclusive"]


def _none_if_blank(value: Any) -> Any:
    """Agents echo the contract's `"... or null"` literally; treat that as null."""
    if isinstance(value, str) and value.strip().lower() in {"", "null", "none"}:
        return None
    return value


class Reply(BaseModel):
    """A workflow's reply: lenient about extra keys, strict about typed ones."""

    model_config = ConfigDict(extra="allow")

    nullable: ClassVar[tuple[str, ...]] = ()

    @classmethod
    def check(cls, data: dict[str, Any], *, workflow: str) -> None:
        """Raise `AgentError` listing every way `data` breaks this contract."""
        cleaned = {
            key: _none_if_blank(value) if key in cls.nullable else value
            for key, value in data.items()
        }
        try:
            cls.model_validate(cleaned)
        except ValidationError as exc:
            problems = "\n".join(
                f"  - {'.'.join(str(part) for part in error['loc']) or '(reply)'}: "
                f"{error['msg']}"
                for error in exc.errors()
            )
            raise AgentError(
                f"The agent's `{workflow}` reply does not match its output contract "
                f"(prompts/{workflow}.md), so nothing was written:\n{problems}"
            ) from exc
        data.update({key: cleaned[key] for key in cls.nullable if key in cleaned})


class Item(BaseModel):
    """One record the reply asks to create: it must at least name its id."""

    model_config = ConfigDict(extra="allow")

    id: str

    @field_validator("id")
    @classmethod
    def _id_not_blank(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("must be a non-empty id")
        return value.strip()


class DraftReply(Reply):
    id: str | None = None
    metrics: list[Any] | None = None


class DesignItem(Item):
    parent: str | None = None
    open_items: list[Any] | None = None


class DesignReply(Reply):
    experiments: list[DesignItem]


class DiscoverItem(Item):
    proposed_parent: str | None = None


class DiscoverReply(Reply):
    nullable = ("injected_instructions_found",)

    proposals: list[DiscoverItem] | None = None
    declined: list[Item] | None = None
    investigation: dict[str, Any] | None = None
    injected_instructions_found: str | list[Any] | None = None


class ImplementReply(Reply):
    nullable = ("blocked", "commit")

    blocked: Any = None
    commit: str | None = None
    entrypoints: dict[str, Any] | None = None

    @model_validator(mode="after")
    def _commit_unless_blocked(self) -> ImplementReply:
        if not self.blocked and not (self.commit or "").strip():
            raise ValueError("`commit` is required unless the reply is `blocked`")
        return self


class TriageReply(Reply):
    nullable = ("blocked", "failure_class", "verdict")

    blocked: Any = None
    failure_class: Literal["A", "B", "C", "a", "b", "c"] | None = None
    verdict: Verdict | None = None
    proceed: bool | None = None
    children: list[Item] | None = None


class AnalyzeReply(Reply):
    nullable = ("verdict", "promotable_candidate")

    verdict: Verdict | None = None


class ReviseReply(Reply):
    nodes: dict[str, Any]
