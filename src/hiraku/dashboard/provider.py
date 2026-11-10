"""Plain HTML Studio views with styles, scripts, and an assets directory."""

import html
from pathlib import PurePosixPath

from marimo_studio.view_providers import (
    BuildInput,
    BuildRequest,
    BuildResult,
    InspectionRequest,
    ProjectInspection,
    ProviderAvailability,
    ProviderError,
    ProviderInfo,
    ProviderStarter,
    SourceDocument,
    StarterContext,
    StarterPlan,
    copy_inputs,
    html_sites,
    project_files,
)

ENTRY = PurePosixPath("index.html")
LANGUAGES = {".html": "html", ".css": "css", ".js": "javascript"}


class HtmlProvider:
    """Copy root HTML/CSS/JS and assets without a frontend build tool."""

    info = ProviderInfo(
        title="HTML with assets",
        summary="Plain HTML, CSS, JavaScript, and local assets.",
    )

    def availability(self) -> ProviderAvailability:
        return ProviderAvailability(True)

    def starters(self) -> tuple[ProviderStarter, ...]:
        return (
            ProviderStarter(
                key="default",
                title="HTML with assets",
                summary="A minimal page displaying notebook cells.",
                documents=(ENTRY,),
            ),
        )

    def create(self, starter: ProviderStarter, context: StarterContext) -> StarterPlan:
        targets = context.output_cells
        cells = "\n".join(
            f'<marimo-cell name="{html.escape(target.target, quote=True)}">'
            "</marimo-cell>"
            for target in targets
        )
        page = (
            '<!doctype html><html lang="en"><head><meta charset="utf-8">'
            "<title>Dashboard</title></head><body>"
            f'<main id="app-shell">{cells}</main></body></html>'
        )
        return StarterPlan(files={ENTRY: page.encode()}, cell_targets=targets)

    def inspect(self, request: InspectionRequest) -> ProjectInspection:
        files = project_files(request.project)
        sources = tuple(
            path for path in files if len(path.parts) == 1 and path.suffix in LANGUAGES
        )
        if ENTRY not in sources:
            raise ProviderError("The HTML view requires index.html.")
        source = (request.project.root / "index.html").read_bytes()
        sites, diagnostics = html_sites(ENTRY, source)
        inputs = [BuildInput(path, "file") for path in sources]
        if (request.project.root / "assets").is_dir():
            inputs.append(BuildInput(PurePosixPath("assets"), "directory"))
        return ProjectInspection(
            documents=tuple(
                SourceDocument(path, LANGUAGES[path.suffix], "edit") for path in sources
            ),
            inputs=tuple(inputs),
            sites=sites,
            diagnostics=diagnostics,
        )

    def build(self, request: BuildRequest) -> BuildResult:
        copy_inputs(request, request.staging_root)
        return BuildResult(ENTRY, ())


provider = HtmlProvider()
