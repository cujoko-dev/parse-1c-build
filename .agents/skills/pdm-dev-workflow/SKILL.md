---
name: pdm-dev-workflow
description: >-
  PDM with a nested `.dev` project: installing dependencies and running tests
  or scripts via `pdm run -p .dev` without activating the venv. Use when the
  repository has `.dev/pyproject.toml` and the task touches Python,
  dependencies, or tests.
---

# PDM and the `.dev` project

- The root `pyproject.toml` is the package.
- Development tools (pytest, scripts in `[tool.pdm.scripts]`) live in the nested
  `.dev/pyproject.toml`, with its own lock and `.dev/.venv`.
- Runtime dependencies: `pdm install` or `pdm sync` from the root.
- Dev dependencies: `pdm install -p .dev`.
- Run tests and dev scripts with `pdm run -p .dev <script>` or
  `pdm run -p .dev pytest …`. Never activate the venv by hand.
- If the repository has `scripts/run.ps1`, run tests and builds through it. See
  "Test and build runs go through `scripts/run.ps1`" in `AGENTS.md`.
- No `uv`, Poetry, or ad-hoc `pip install` for the project lock. See "Python
  package manager: PDM" in `AGENTS.md`.
