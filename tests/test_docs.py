import re
import shutil
import subprocess
import sys
from pathlib import Path
from urllib.parse import urljoin

import pytest

REPOSITORY = Path(__file__).parents[1]
SYMBOL = "assets/SDSC_04_symbol-color-RGB.svg"


@pytest.mark.integration
def test_docs_branding_links_from_nested_urls(tmp_path: Path) -> None:
    pytest.importorskip("zensical")
    shutil.copytree(REPOSITORY / "overrides", tmp_path / "overrides")
    docs = tmp_path / "docs"
    (docs / "assets").mkdir(parents=True)
    shutil.copyfile(REPOSITORY / "docs" / SYMBOL, docs / SYMBOL)
    (docs / "index.md").write_text("# Home\n", encoding="utf-8")
    (docs / "guide.md").write_text("# Guide\n", encoding="utf-8")
    (tmp_path / "zensical.toml").write_text(
        '[project]\nsite_name = "Hiraku"\nsite_url = "https://example.com/hiraku/"\n'
        '[project.theme]\ncustom_dir = "overrides"\n'
        f'favicon = "{SYMBOL}"\n'
        '[project.extra]\nsdsc_url = "https://datascience.ch"\n',
        encoding="utf-8",
    )

    subprocess.run(
        [sys.executable, "-m", "zensical", "build"], cwd=tmp_path, check=True
    )

    site = tmp_path / "site"
    html = (site / "404.html").read_text(encoding="utf-8")
    logo = re.search(r'sdsc-error__brand["\']?>\s*<img src=["\']?([^\s"\'>]+)', html)
    assert logo is not None
    missing_url = "https://preview.example/hiraku/missing/nested/page/"
    assert urljoin(missing_url, logo[1]) == f"https://preview.example/hiraku/{SYMBOL}"
    assert (site / SYMBOL).is_file()

    guide_url = "https://preview.example/hiraku/guide/"
    guide = (site / "guide" / "index.html").read_text(encoding="utf-8")
    for page, url in ((html, missing_url), (guide, guide_url)):
        home = re.search(r'md-header__home"\s+href=["\']?([^\s"\'>]+)', page)
        assert home is not None
        assert urljoin(url, home[1]) == "https://preview.example/hiraku/"
        credit = re.search(r'md-header__sdsc"\s+href=["\']?([^\s"\'>]+)', page)
        assert credit is not None
        assert credit[1] == "https://datascience.ch"
