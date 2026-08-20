from pathlib import Path

from conftest import run_process


def test_init_generates_project_assets(repo: Path, hiraku_command: list[str]) -> None:
    result = run_process([*hiraku_command, "init"], repo)

    assert "Initialized Hiraku" in result.stdout
    assert (repo / ".hiraku.yml").is_file()
    assert (repo / "experiments/template.md").is_file()
    assert (repo / ".hiraku/policies/guided.md").is_file()
    assert (repo / ".hiraku/.gitignore").read_text() == "runs.csv\nruns.tsv\n"
    skill = (repo / ".agents/skills/hiraku/SKILL.md").read_text(encoding="utf-8")
    assert "references/auto-loop.md" in skill


def test_init_fails_without_overwriting(repo: Path, hiraku_command: list[str]) -> None:
    run_process([*hiraku_command, "init"], repo)
    before = (repo / ".hiraku.yml").read_bytes()

    result = run_process([*hiraku_command, "init"], repo, check=False)

    assert result.returncode == 1
    assert "would overwrite or conflict" in result.stderr
    assert (repo / ".hiraku.yml").read_bytes() == before


def test_init_requires_git_root(repo: Path, hiraku_command: list[str]) -> None:
    child = repo / "child"
    child.mkdir()

    result = run_process([*hiraku_command, "init"], child, check=False)

    assert result.returncode == 1
    assert "Git repository root" in result.stderr
    assert not (child / ".hiraku.yml").exists()


def test_init_preflights_all_collisions(repo: Path, hiraku_command: list[str]) -> None:
    template = repo / "experiments/template.md"
    template.parent.mkdir()
    template.write_text("user content\n", encoding="utf-8")

    result = run_process([*hiraku_command, "init"], repo, check=False)

    assert result.returncode == 1
    assert "experiments/template.md" in result.stderr
    assert template.read_text() == "user content\n"
    assert not (repo / ".hiraku.yml").exists()
