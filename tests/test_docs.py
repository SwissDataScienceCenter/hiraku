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
def test_404_logo_uses_current_origin_from_nested_url(tmp_path: Path) -> None:
    pytest.importorskip("zensical")
    shutil.copytree(REPOSITORY / "overrides", tmp_path / "overrides")
    docs = tmp_path / "docs"
    (docs / "assets").mkdir(parents=True)
    shutil.copyfile(REPOSITORY / "docs" / SYMBOL, docs / SYMBOL)
    (docs / "index.md").write_text("# Home\n", encoding="utf-8")
    (tmp_path / "zensical.toml").write_text(
        '[project]\nsite_name = "Hiraku"\nsite_url = "https://example.com/hiraku/"\n'
        '[project.theme]\ncustom_dir = "overrides"\n'
        f'favicon = "{SYMBOL}"\n',
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
