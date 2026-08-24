from __future__ import annotations

import pytest

from hiraku_auto import yamlio
from hiraku_auto.errors import HirakuError
from hiraku_auto.records import load_experiment, read_record, save_experiment, validate_id


def test_front_matter_round_trip():
    text = '---\nid: a-b\nmode: guided\nnested:\n  x: 1\n---\n\n# Title\n\n## One\n\nbody\n'
    front, body = yamlio.split_front_matter(text)
    assert front["id"] == "a-b"
    assert front["nested"] == {"x": 1}
    assert body.startswith("# Title")
    assert yamlio.split_front_matter(yamlio.join_front_matter(front, body))[0] == front


def test_unterminated_front_matter_is_an_error():
    with pytest.raises(HirakuError):
        yamlio.split_front_matter("---\nid: x\n\nno closing delimiter\n")


def test_fallback_parser_matches_pyyaml():
    text = "id: a\nnum: 3\nflag: true\nlist: [x, y]\nnested:\n  deep: 2\nempty:\n"
    assert yamlio._fallback_load(text) == {
        "id": "a",
        "num": 3,
        "flag": True,
        "list": ["x", "y"],
        "nested": {"deep": 2},
        "empty": None,
    }


@pytest.mark.parametrize("value", ["ok", "a-b-c", "vggt3"])
def test_valid_ids(value):
    assert validate_id(value) == value


@pytest.mark.parametrize("value", ["Bad", "a_b", "a--b", "-a", "a-", ""])
def test_invalid_ids(value):
    with pytest.raises(HirakuError):
        validate_id(value)


def test_sections_survive_a_round_trip(project):
    record = load_experiment(project, "candidate")
    record.set_section("Findings", "the finding")
    save_experiment(record)
    again = load_experiment(project, "candidate")
    assert again.section("Findings").strip() == "the finding"
    # Front-matter order is stable, so diffs stay small.
    assert again.render().splitlines()[1].startswith("id:")


def test_append_section_keeps_existing_text(project):
    record = load_experiment(project, "candidate")
    record.set_section("Findings", "first")
    record.append_section("Findings", "second")
    assert "first" in record.section("Findings")
    assert "second" in record.section("Findings")


def test_directory_id_mismatch_is_rejected(project):
    path = project.experiments_dir / "candidate" / "experiment.md"
    record = read_record(path)
    record.front["id"] = "renamed"
    path.write_text(record.render(), encoding="utf-8")
    with pytest.raises(HirakuError, match="disagrees with directory"):
        load_experiment(project, "candidate")
