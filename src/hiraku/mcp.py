"""MCP tools for Hiraku agentic research operations."""

from __future__ import annotations

import tomllib
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from mcp.server.mcpserver import MCPServer
from pydantic import BaseModel, Field

from .config import HirakuConfig
from .errors import ValidationError
from .git import GitRepository
from .io import atomic_write
from .launch import launch
from .record import Record, list_records, load_record


class ProposedEntrypoint(BaseModel):
    name: str = Field(min_length=1)
    command: list[str] = Field(min_length=1)
    args: dict[str, Any] = Field(default_factory=dict)


class ExperimentProposal(BaseModel):
    id: str = Field(min_length=1)
    parent_id: str
    summary: str = Field(min_length=1)
    hypothesis: str = Field(min_length=1)
    entrypoint: ProposedEntrypoint | None = None


class Analysis(BaseModel):
    run_ids: list[str] = Field(min_length=1)
    conclusion: str = Field(min_length=1)
    decision: str = Field(min_length=1)


@dataclass(frozen=True, slots=True)
class MCPConfigInstall:
    """Filesystem changes made while installing Codex MCP configuration."""

    created_files: tuple[Path, ...] = ()
    created_dirs: tuple[Path, ...] = ()


class HirakuMCPServer:
    """Hiraku's typed MCP API and harness configuration integration."""

    def __init__(self, config: HirakuConfig | None = None) -> None:
        self.config = config or HirakuConfig.discover()
        self.server = MCPServer(
            name="hiraku",
            title="Hiraku MCP Server",
            description="Hiraku research state and operations",
            instructions="Hiraku research state and operations",
        )
        for handler in (
            self.get_record,
            self.resolve_record,
            self.list_children,
            self.list_runs,
            self.propose_experiment,
            self.approve_experiment,
            self.commit_implementation,
            self.launch_experiment,
            self.record_analysis,
        ):
            self.server.tool()(handler)

    @staticmethod
    def install_codex_config(project_root: Path) -> MCPConfigInstall:
        """Install Codex's project-local stdio entry without replacing settings."""
        codex_dir = project_root / ".codex"
        config_path = codex_dir / "config.toml"
        if codex_dir.is_symlink() or config_path.is_symlink():
            raise ValidationError(
                f"Codex configuration path cannot be a symbolic link: {config_path}"
            )

        server_config = '[mcp_servers.hiraku]\ncommand = "hiraku"\nargs = ["mcp"]\n'
        created_dirs: tuple[Path, ...] = ()
        if not config_path.exists():
            if not codex_dir.exists():
                codex_dir.mkdir()
                created_dirs = (codex_dir,)
            try:
                atomic_write(config_path, server_config)
            except Exception:
                for directory in reversed(created_dirs):
                    directory.rmdir()
                raise
            return MCPConfigInstall((config_path,), created_dirs)
        if not config_path.is_file():
            raise ValidationError(f"Codex configuration is not a file: {config_path}")

        content = config_path.read_text(encoding="utf-8")
        try:
            settings = tomllib.loads(content)
        except tomllib.TOMLDecodeError as exc:
            raise ValidationError(
                f"Cannot add Hiraku MCP to invalid Codex config {config_path}: {exc}"
            ) from exc
        servers = settings.get("mcp_servers", {})
        if not isinstance(servers, dict):
            raise ValidationError(
                f"Invalid mcp_servers table in Codex config: {config_path}"
            )
        if "hiraku" in servers:
            return MCPConfigInstall()

        separator = "\n" if content.endswith("\n") or not content else "\n\n"
        atomic_write(config_path, f"{content.rstrip()}{separator}{server_config}")
        return MCPConfigInstall()

    def get_record(self, record_id: str) -> dict[str, Any]:
        """Return a record's stored and inherited research state."""
        record = load_record(record_id, config=self.config)
        resolved = record.resolve(config=self.config)
        return {
            "record": record.to_dict(),
            "effective": resolved.values,
            "sources": resolved.sources,
            "markdown": record.markdown,
        }

    def resolve_record(self, reference: str) -> dict[str, Any]:
        """Resolve a record ID or an unambiguous ID prefix."""
        records = list_records(config=self.config)
        matches = [item for item in records if item.id == reference]
        if not matches:
            matches = [item for item in records if item.id.startswith(reference)]
        if len(matches) != 1:
            raise ValidationError(
                f"Record reference {reference!r} matched {len(matches)} records"
            )
        return {"id": matches[0].id, "path": str(matches[0].path)}

    def list_children(self, record_id: str) -> list[dict[str, Any]]:
        """List direct child records."""
        parent = load_record(record_id, config=self.config)
        return [
            {
                "id": item.id,
                "type": item.type,
                "status": item.status,
                "commit": item.commit,
            }
            for item in parent.children(config=self.config)
        ]

    def list_runs(self, record_id: str, limit: int = 100) -> list[dict[str, Any]]:
        """List normalized runs visible from this record."""
        if limit < 0:
            raise ValidationError("limit must be non-negative")
        return [
            self._run_data(run)
            for run in load_record(record_id, config=self.config).runs(
                config=self.config, limit=limit
            )
        ]

    def propose_experiment(self, proposal: ExperimentProposal) -> dict[str, Any]:
        """Create a child experiment with its own optional entrypoint definition."""
        parent = load_record(proposal.parent_id, config=self.config)
        if any(item.id == proposal.id for item in list_records(config=self.config)):
            raise ValidationError(f"Record {proposal.id!r} already exists")
        values: dict[str, Any] = {
            "type": "experiment",
            "status": "proposed",
            "hypothesis": proposal.hypothesis,
            "proposal_summary": proposal.summary,
            "approval_status": "pending",
        }
        if proposal.entrypoint:
            values["entrypoints"] = {
                proposal.entrypoint.name: {
                    "command": proposal.entrypoint.command,
                    "args": proposal.entrypoint.args,
                }
            }
        record = Record.create(
            proposal.id,
            parent_id=parent.id,
            markdown=proposal.summary,
            activate=False,
            config=self.config,
            **values,
        )
        return {
            "id": record.id,
            "status": record.status,
            "approval_status": record.approval_status,
        }

    def approve_experiment(self, record_id: str, approved_by: str) -> dict[str, Any]:
        """Record explicit human approval for a proposed experiment."""
        if not approved_by.strip():
            raise ValidationError("approved_by must identify the approving user")
        record = load_record(record_id, config=self.config)
        if record.type != "experiment" or record.approval_status != "pending":
            raise ValidationError("Only a pending experiment proposal can be approved")
        record.approval_status = "approved"
        record.approved_by = approved_by
        record.approved_at_head = GitRepository(self.config).head
        record.status = "approved"
        record.save()
        return {
            "id": record.id,
            "approval_status": record.approval_status,
            "approved_by": record.approved_by,
            "approved_at_head": record.approved_at_head,
        }

    def commit_implementation(
        self, record_id: str, paths: list[str], message: str
    ) -> dict[str, Any]:
        """Validate and commit explicitly selected implementation files."""
        record = load_record(record_id, config=self.config)
        if record.type != "experiment" or record.approval_status != "approved":
            raise ValidationError("Implementation requires an approved experiment")
        if not paths or not message.strip():
            raise ValidationError("paths and message must be non-empty")
        git = GitRepository(self.config)
        if git.head != record.approved_at_head:
            raise ValidationError(
                "HEAD changed since approval; propose and approve against current state"
            )
        resolved_paths: list[str] = []
        for path in paths:
            candidate = (self.config.root / path).resolve()
            try:
                candidate.relative_to(self.config.root.resolve())
            except ValueError as exc:
                raise ValidationError(f"Path is outside the project: {path}") from exc
            if (
                candidate == self.config.records_dir
                or self.config.records_dir in candidate.parents
            ):
                raise ValidationError(
                    "Research records cannot be included as implementation"
                )
            if not candidate.is_file():
                raise ValidationError(f"Implementation file does not exist: {path}")
            resolved_paths.append(
                str(candidate.relative_to(self.config.root.resolve()))
            )
        staged_paths = {
            path
            for path in git._git("diff", "--cached", "--name-only", "-z").stdout.split(
                "\0"
            )
            if path
        }
        unrelated_staged = staged_paths.difference(resolved_paths)
        if unrelated_staged:
            raise ValidationError(
                f"Refusing to commit unrelated staged paths: {sorted(unrelated_staged)}"
            )
        git._git("add", "--", *resolved_paths)
        git._git("diff", "--cached", "--check")
        git._git(
            "-c",
            "commit.gpgSign=false",
            "commit",
            "-m",
            message,
        )
        commit = git.head
        record.commit = commit
        record.status = "implemented"
        record.save()
        return {
            "record_id": record.id,
            "commit": commit,
            "committed_paths": resolved_paths,
        }

    def launch_experiment(
        self,
        record_id: str,
        entrypoint: str,
        args: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Launch an approved, committed experiment through its dispatcher."""
        record = load_record(record_id, config=self.config)
        if (
            record.type != "experiment"
            or record.approval_status != "approved"
            or not record.commit
        ):
            raise ValidationError(
                "Launch requires an approved experiment with a commit"
            )
        git = GitRepository(self.config)
        commit = git.resolve(record.commit)
        if commit != record.commit:
            raise ValidationError("Experiment commit is invalid")
        definition = record.entrypoint(entrypoint, config=self.config)
        result = launch(
            definition,
            name=entrypoint,
            args=args or {},
            record_id=record.id,
            commit=commit,
            capture_output=True,
            check=False,
            config=self.config,
        )
        if result.returncode:
            raise ValidationError(
                f"Experiment launch exited with status {result.returncode}"
            )
        return {
            "record_id": record.id,
            "commit": commit,
            "entrypoint": entrypoint,
            "returncode": result.returncode,
            "stdout": result.stdout,
            "stderr": result.stderr,
        }

    def record_analysis(self, record_id: str, analysis: Analysis) -> dict[str, Any]:
        """Validate cited runs and persist the experiment conclusion."""
        record = load_record(record_id, config=self.config)
        if record.type != "experiment" or not record.commit:
            raise ValidationError("Analysis requires a committed experiment")
        known = {run.id: run for run in record.runs(config=self.config)}
        cited = []
        for run_id in analysis.run_ids:
            run = known.get(run_id)
            if run is None or run.record_id != record.id or run.commit != record.commit:
                raise ValidationError(
                    f"Run {run_id!r} is not attributed to this experiment commit"
                )
            cited.append(run_id)
        record.conclusion = analysis.conclusion
        record.decision = analysis.decision
        record.analysis_run_ids = cited
        record.status = "analyzed"
        record.save()
        return {
            "record_id": record.id,
            "status": record.status,
            "run_ids": cited,
            "conclusion": record.conclusion,
            "decision": record.decision,
        }

    @staticmethod
    def _run_data(run: Any) -> dict[str, Any]:
        return {
            "id": run.id,
            "record_id": run.record_id,
            "commit": run.commit,
            "entrypoint": run.entrypoint,
            "metrics": dict(run.metrics),
            "params": dict(run.params),
            "timestamp": run.timestamp,
            "url": run.url,
        }


def create_server(config: HirakuConfig | None = None) -> MCPServer:
    """Create the local MCP server bound to one discovered Hiraku project."""
    return HirakuMCPServer(config).server


def serve(config: HirakuConfig | None = None) -> None:
    """Run Hiraku's local stdio MCP server."""
    HirakuMCPServer(config).server.run(transport="stdio")
