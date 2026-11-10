"""Exercise HTML assets through Studio's inspection and publication pipeline."""

import re
from dataclasses import replace
from pathlib import Path, PurePosixPath
from types import SimpleNamespace

import pytest

pytest.importorskip("marimo_studio")

from marimo_studio.view_providers import ProviderError
from marimo_studio.view_providers.testing import check_provider

from hiraku.dashboard.launcher import TEMPLATES
from hiraku.dashboard.provider import HtmlProvider

BUNDLED_VIEWS = Path(str(TEMPLATES / "dashboard" / "studio"))


class BundledProvider(HtmlProvider):
    def create(self, starter, context):
        plan = super().create(starter, context)
        root = BUNDLED_VIEWS / "dashboard"
        files = {
            PurePosixPath(path.relative_to(root).as_posix()): path.read_bytes()
            for path in root.rglob("*")
            if path.is_file() and path.suffix in {".html", ".css", ".svg", ".js"}
        }
        # Keep the bundled layout, using the test notebook's output cell hosts.
        page = files[PurePosixPath("index.html")].decode()
        page = re.sub(r"<marimo-cell\b[^>]*></marimo-cell>", "", page)
        starter_page = plan.files[PurePosixPath("index.html")].decode()
        cells = starter_page.split('<main id="app-shell">')[1].split("</main>")[0]
        page = page.replace("</main>", cells + "</main>")
        files[PurePosixPath("index.html")] = page.encode()
        return replace(plan, files=files)


def test_registered_provider_publishes_starter():
    views = check_provider("hiraku/html")
    assert PurePosixPath("index.html") in views[0].published


def test_bundled_view_publishes_linked_images_and_styles():
    view = check_provider(BundledProvider())[0]
    root = BUNDLED_VIEWS / "dashboard"
    for name in ("sdsc-logo.svg", "sdsc-logo-white.svg"):
        path = PurePosixPath("assets") / name
        assert view.published[path] == (root / str(path)).read_bytes()
    assert b"data:image" not in view.published[PurePosixPath("index.html")]
    assert PurePosixPath("style.css") in view.published


def test_inspection_tracks_asset_directory_and_omits_unrelated_files(tmp_path):
    (tmp_path / "index.html").write_text(
        '<html><head></head><body><main id="app-shell"></main></body></html>'
    )
    (tmp_path / "assets").mkdir()
    (tmp_path / "assets/image.png").write_bytes(b"image")
    (tmp_path / "private.csv").write_text("not a dashboard asset")
    request = SimpleNamespace(project=SimpleNamespace(root=tmp_path))

    inspection = HtmlProvider().inspect(request)

    assert {(str(item.path), item.kind) for item in inspection.inputs} == {
        ("index.html", "file"),
        ("assets", "directory"),
    }


def test_inspection_rejects_asset_symlinks(tmp_path):
    (tmp_path / "index.html").write_text("<html></html>")
    (tmp_path / "assets").mkdir()
    (tmp_path / "private.csv").write_text("private")
    (tmp_path / "assets/image.svg").symlink_to(tmp_path / "private.csv")
    request = SimpleNamespace(project=SimpleNamespace(root=tmp_path))

    with pytest.raises(ProviderError):
        HtmlProvider().inspect(request)


def test_inspection_requires_entry_document(tmp_path):
    request = SimpleNamespace(project=SimpleNamespace(root=tmp_path))
    with pytest.raises(ProviderError, match="requires index.html"):
        HtmlProvider().inspect(request)
