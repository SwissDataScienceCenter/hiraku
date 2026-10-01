"""Program records and their registry operations."""

from __future__ import annotations

from pathlib import Path

from ..base import BaseRecord
from ..config import HirakuConfig
from ..errors import ValidationError
from ..validation import validate_identifier


class Program(BaseRecord):
    """An editable agent program record."""


def load_program(program_id: str, *, config: HirakuConfig | None = None) -> Program:
    selected = config or HirakuConfig.discover()
    validate_identifier(program_id, label="program ID")
    path = selected.programs_dir / program_id / "program.md"
    program = Program.load(path)
    _validate_identity(program, program_id, path)
    return program


def list_programs(*, config: HirakuConfig | None = None) -> list[Program]:
    selected = config or HirakuConfig.discover()
    programs = Program.list(selected.programs_dir.glob("*/program.md"))
    for program in programs:
        _validate_identity(
            program, program.id, selected.programs_dir / program.id / "program.md"
        )
    return programs


def _validate_identity(record: BaseRecord, identifier: str, path: Path) -> None:
    if record.id != identifier or record.path != path.absolute():
        raise ValidationError(f"Program identity does not match registry path: {path}")
