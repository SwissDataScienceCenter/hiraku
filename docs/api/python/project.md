# Project initialization API

`Project.create(path)` initializes a project, while `Project.discover(path)`
finds an existing one. The project object owns its persisted settings and paths.
Creation also prepares the root `.hiraku/` directory for future project data.

## `Project`

::: hiraku.project.Project
