from __future__ import annotations

import fnmatch
from pathlib import Path
from typing import Any

DEFAULT_PATTERNS = (
    "README*",
    "pyproject.toml",
    "requirements*.txt",
    "uv.lock",
    "poetry.lock",
    "environment*.yml",
    "Dockerfile*",
    "Makefile",
    "launch*.yml",
    "launch*.yaml",
    "*.py",
    "*.ipynb",
    "src/**",
    "app/**",
    "scripts/**",
    "examples/**",
)
EXCLUDED_PARTS = {".git", ".venv", "venv", "node_modules", ".hiraku", "__pycache__"}
SECRET_NAMES = {".env", ".env.local", ".env.production", "credentials.json"}


def collect_project_context(root: Path, *, max_files: int = 80, max_bytes: int = 120_000) -> dict[str, Any]:
    """Return a bounded, secret-conscious snapshot suitable for an agent prompt."""
    files: list[dict[str, str]] = []
    used = 0
    candidates = sorted(path for path in root.rglob("*") if path.is_file())
    for path in candidates:
        relative = path.relative_to(root)
        if (
            any(part in EXCLUDED_PARTS for part in relative.parts)
            or path.name in SECRET_NAMES
            or path.name.startswith(".env.")
            or path.suffix.lower() in {".pem", ".key", ".p12"}
        ):
            continue
        if not any(
            fnmatch.fnmatch(str(relative), pattern) or fnmatch.fnmatch(path.name, pattern)
            for pattern in DEFAULT_PATTERNS
        ):
            continue
        if len(files) >= max_files or used >= max_bytes:
            break
        try:
            data = path.read_bytes()
        except OSError:
            continue
        if b"\x00" in data:
            continue
        remaining = max_bytes - used
        text = data[:remaining].decode("utf-8", errors="replace")
        files.append({"path": str(relative), "content": text})
        used += len(text.encode("utf-8"))
    return {
        "root_name": root.name,
        "files": files,
        "file_count": len(files),
        "truncated": len(files) >= max_files or used >= max_bytes,
        "limits": {"max_files": max_files, "max_bytes": max_bytes},
    }
