# Tinta MCP agent guide

Tinta is a Model Context Protocol (MCP) server that enables AI assistants to control running Inkscape instances in real time via D-Bus.

## Repository overview

- `tintamcp/`: Python MCP server package (`fastmcp`). Runs in the user's external Python environment, exposes tools, and translates requests into D-Bus calls.
- `extension/`: Inkscape extension (`tinta_mcp.py`, `tinta_mcp.inx`, and `tinta_mcp_ops/`). Runs inside Inkscape's internal Python interpreter when triggered by D-Bus.
- `tests/`: Automated unit tests and live D-Bus smoke tests using `pytest`.
- `scripts/`: Setup scripts for Windows (`install_windows.ps1`, `install_windows.bat`) and Linux/WSL (`install_linux.sh`).
- `.agents/`: Project rules, guidelines, and skills for AI assistants and contributors.
- `.github/workflows/`: CI matrix tests, security audits, headless D-Bus integration tests, and release packaging.

## Core rules and invariants

1. Runtime separation:
   `tintamcp` must remain pure Python. Never add `inkex`, `lxml`, `pycairo`, or `pygobject` to `[project.dependencies]` in `pyproject.toml`. The `inkex` module is only imported inside `extension/` because it is provided by Inkscape at runtime.
2. D-Bus identifier:
   The registered D-Bus action name is strictly `org.tinta.mcp`. Do not introduce backwards-compatibility aliases (`inkmcp`).
3. Cross-platform parity:
   Support both Windows and Linux. On Windows, D-Bus calls use Inkscape's bundled `gdbus.exe`. On Linux, D-Bus calls use the system session bus `gdbus`. IPC lives under `%LOCALAPPDATA%\Temp\tintamcp\<user>` on Windows, or a verified private `$XDG_RUNTIME_DIR/tintamcp` with `/tmp/tintamcp-<uid>` fallback on Linux.
4. Security and sanitization:
   Validate XML element and attribute names using `is_valid_xml_name()`. Escape attribute values properly before inserting into SVG trees. Python code execution and textual command tools are not supported. Never evaluate client-provided Python with `exec`/`eval` or resolve operation names to arbitrary Python callables. Use fixed extension handlers receiving structured data; operation names and SVG element tags are separate fields.
5. Code quality gates:
   When modifying Python code (`extension/`, `tintamcp/`, `tests/`) or dependencies (`pyproject.toml`), verify that all quality gates pass:
   ```bash
   uv run ruff check extension tintamcp tests
   uv run ruff format --check extension tintamcp tests
   uv run pyright
   uv run pytest
   uv run pip-audit
   ```
   Pure documentation edits (such as updating `.md` files) do not require running Python test or lint gates.

## Technical prose and documentation style

When writing documentation, guides, commit messages, or agent responses:
- State facts directly without rhetorical staging (*not X but Y*, fake alternatives, or dramatic one-line closers).
- Use sentence case for headings, avoid decorative emojis, and do not divide sections with horizontal rules (`---`).
- Do not use em dashes (`—`) or double hyphens (`--`) as universal connectors. Use colons, commas, periods, or parentheses instead.
- Write list items as natural sentences without forcing a bold title and colon on every bullet.
- Maintain a neutral, factual tone without marketing filler or AI buzzwords (*robust*, *seamless*, *testament*, *pivotal*, *delve*, *cutting-edge*, *effortless*).

## Project rules and references

- Architecture and IPC boundaries: [rules/architecture.md](rules/architecture.md)

## Project skills

- `mcp-tool-builder` ([skills/mcp-tool-builder/SKILL.md](skills/mcp-tool-builder/SKILL.md)): Workflow for adding, modifying, and testing MCP tools for Inkscape.
- `tinta-designer` ([skills/tinta-designer/SKILL.md](skills/tinta-designer/SKILL.md)): Best practices and technical guidelines for creating and editing vector graphics in Inkscape via Tinta.
- `tinta-dev-workflow` ([skills/tinta-dev-workflow/SKILL.md](skills/tinta-dev-workflow/SKILL.md)): Development standards, `uv` environment management, Conventional Commits, and CI quality gates.

## Releasing a new version

1. Bump version in `pyproject.toml` (`version = "X.Y.Z"`).
2. Verify quality gates pass: `uv run pytest && uv run ruff check extension tintamcp tests && uv run pyright`.
3. Commit using Conventional Commits: `git commit -am "chore(release): bump version to X.Y.Z"`.
4. Tag and push: `git tag vX.Y.Z && git push origin main --tags`.
5. GitHub Actions builds wheels, packages `tinta-extension.zip`, and creates the GitHub Release automatically.
