from __future__ import annotations

from pathlib import Path

import pytest

from hiraku.errors import ValidationError
from hiraku.io import atomic_write, dump_yaml, load_yaml_mapping


class TestYamlAndAtomicFileBehavior:
    def test_yaml_mapping_loads_mapping_and_treats_empty_file_as_empty_mapping(
        self, tmp_path: Path
    ) -> None:
        mapping = tmp_path / "mapping.yml"
        mapping.write_text("answer: 42\n", encoding="utf-8")
        empty = tmp_path / "empty.yml"
        empty.write_text("", encoding="utf-8")

        assert load_yaml_mapping(mapping) == {"answer": 42}
        assert load_yaml_mapping(empty) == {}

    @pytest.mark.parametrize(
        ("content", "message"),
        [("[1, 2]", "Expected a YAML mapping"), ("key: [", "Cannot read YAML mapping")],
    )
    def test_yaml_mapping_rejects_non_mapping_and_malformed_documents(
        self, tmp_path: Path, content: str, message: str
    ) -> None:
        path = tmp_path / "invalid.yml"
        path.write_text(content, encoding="utf-8")

        with pytest.raises(ValidationError, match=message):
            load_yaml_mapping(path)

    def test_yaml_mapping_wraps_file_read_failure(self, tmp_path: Path) -> None:
        with pytest.raises(ValidationError, match="Cannot read YAML mapping"):
            load_yaml_mapping(tmp_path / "missing.yml")

    def test_yaml_dump_preserves_nested_and_unicode_values(self) -> None:
        dumped = dump_yaml({"nested": {"label": "✓"}, "items": [1, 2]})

        assert "✓" in dumped
        assert "nested:" in dumped
        assert "- 1" in dumped
        assert "- 2" in dumped

    def test_yaml_dump_rejects_values_it_cannot_serialize(self) -> None:
        with pytest.raises(ValidationError, match="Unsupported YAML value"):
            dump_yaml({"value": object()})

    def test_atomic_write_creates_parent_and_applies_mode(self, tmp_path: Path) -> None:
        path = tmp_path / "nested" / "result.txt"

        atomic_write(path, "complete\n", mode=0o640)

        assert path.read_text(encoding="utf-8") == "complete\n"
        assert path.stat().st_mode & 0o777 == 0o640

    def test_failed_replace_keeps_original_and_cleans_temporary_file(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        path = tmp_path / "result.txt"
        path.write_text("original", encoding="utf-8")

        def fail_replace(source: Path, destination: Path) -> None:
            raise OSError("simulated replace failure")

        monkeypatch.setattr("hiraku.io.os.replace", fail_replace)

        with pytest.raises(OSError, match="simulated replace failure"):
            atomic_write(path, "replacement")

        assert path.read_text(encoding="utf-8") == "original"
        assert list(tmp_path.glob(".result.txt.*")) == []
