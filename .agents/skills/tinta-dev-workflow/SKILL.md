---
name: tinta-dev-workflow
description: "Developer and AI agent workflow for dependency management, code quality gates, and conventional commits in Tinta."
---

# Tinta development and quality workflow

Use this skill for local environment setup, running quality gates, and crafting commits that pass CI.

## Environment management with uv

Tinta uses `uv` for fast, reproducible dependency resolution.

### Basic commands

```bash
# Sync all dependencies (runtime + dev tools)
uv sync --all-groups

# Add a runtime dependency (must be pure Python, no C/inkex)
uv add <package-name>

# Add a development tool (linter, test helper)
uv add --group dev <package-name>

# Run commands within the managed environment
uv run pytest
uv run ruff check extension tintamcp tests
```

### Windows and WSL shared drive tip

When developing across both Windows and WSL on a shared drive:
- Windows `.venv` uses `.venv/Scripts/python.exe`.
- Linux `.venv` uses `.venv/bin/python`.
- Running `uv sync` switches between them in 1-2 seconds because all dependencies are pure Python wheels.

## Quality gates checklist

Run these checks when modifying Python code (`extension/`, `tintamcp/`, `tests/`) or dependencies (`pyproject.toml`). Pure documentation edits do not require running test or lint gates.

```bash
# 1. Linting and import sorting
uv run ruff check extension tintamcp tests

# 2. Code formatting
uv run ruff format --check extension tintamcp tests

# 3. Static type analysis
uv run pyright

# 4. Test suite and coverage report
uv run pytest

# 5. Dependency security audit
uv run pip-audit
```

To automatically format files:
```bash
uv run ruff format extension tintamcp tests
uv run ruff check --fix extension tintamcp tests
```

## Conventional Commits standard

All PR titles and commit messages must follow the Conventional Commits specification.

### Format

```text
<type>(<scope>): <short description in imperative mood>

[optional body explaining motivation and context]

[optional footer, e.g. BREAKING CHANGE or Closes #123]
```

### Allowed types

- `feat`: New feature or MCP tool (e.g. `feat(tools): add canvas_rotate tool`)
- `fix`: Bug fix in server, extension, or installer (e.g. `fix(ipc): handle spaces in username path`)
- `docs`: Documentation updates (e.g. `docs: clarify WSL setup in README`)
- `refactor`: Code restructuring without functional changes (e.g. `refactor(parser): extract xml validation`)
- `perf`: Performance improvement (e.g. `perf(export): use native GTK export actions`)
- `test`: Adding or updating tests (e.g. `test(dbus): add smoke test for element deletion`)
- `ci`: CI/CD workflow changes (e.g. `ci: add security audit gate`)
- `chore`: Tooling, dependency, or version updates (e.g. `chore(deps): update fastmcp to 4.1.0`)

### Rules

- Use lower case for type and scope.
- Use imperative present tense (`add`, not `added` or `adds`).
- Do not add a period at the end of the subject line.
