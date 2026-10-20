# Tinta

![Tinta — Inkscape × AI × MCP. Give your AI a vector editor.](https://raw.githubusercontent.com/quenazsoares/tinta-mcp/main/assets/tinta-mcp-banner.png)

[![Python 3.12+](https://img.shields.io/badge/python-3.12+-blue.svg)](https://www.python.org/downloads/)
[![License: AGPL v3](https://img.shields.io/badge/License-AGPL_v3-blue.svg)](https://www.gnu.org/licenses/agpl-3.0)
[![FastMCP 4.0](https://img.shields.io/badge/FastMCP-4.0-green.svg)](https://github.com/jlowin/fastmcp)

Tinta (*ink* in Portuguese) connects AI assistants to an open Inkscape session through MCP. Its structured tools create and edit SVG elements, inspect documents, and export previews using Inkscape.

## Requirements

- Inkscape 1.2 or newer
- Python 3.12 or newer (uv can download a compatible interpreter automatically)
- [uv](https://docs.astral.sh/uv/)

On Windows, Tinta uses the `gdbus.exe` bundled with Inkscape. On Linux, `inkscape` and `gdbus` must be on PATH, with Inkscape and Tinta using the same user session bus. When using WSL, run both in the Linux environment; these instructions do not cover connecting a WSL server to Windows Inkscape.

## Installation

Install the Inkscape extension from the PyPI package:

```bash
uvx tinta-mcp install
```

The installer copies the extension to your Inkscape user extensions directory and offers to install the AI assistant skill in supported agent directories. Add `--yes` to accept skill installation and updates. Creating an additional skill copy and changing MCP configuration require separate confirmation.

Restart Inkscape after installation and open a document, then configure your MCP client below. You do not need to clone the repository or create a virtual environment.

## Client configuration

For Codex and Antigravity App/IDE, the installer can configure the MCP connection after showing the proposed changes and asking for approval:

```bash
uvx tinta-mcp install --configure-mcp
```

To choose a client or preview the installation without changing files:

```bash
uvx tinta-mcp install --agent codex --configure-mcp
uvx tinta-mcp install --agent codex --configure-mcp --dry-run
```

Repeat `--agent` to select more than one client. Existing configuration files are backed up before changes. See [agent integrations](docs/agent-integrations.md) for supported paths, backup restoration, and adding a client to the catalog.

### Manual configuration

Add this entry to your client's MCP configuration, such as `claude_desktop_config.json` or `mcp_config.json`:

```json
{
  "mcpServers": {
    "tinta": {
      "command": "uvx",
      "args": ["tinta-mcp"]
    }
  }
}
```

The client runs `uvx tinta-mcp` to start the server. The `install` subcommand is only needed to set up or update the extension. Make sure `uvx` is on the client's PATH.

Tinta provides structured SVG operations. It does not run arbitrary Python or textual commands, and the `--enable-python` argument is no longer accepted. Remove it from older client configurations.

### AI assistant skill

The installer checks for Codex, Antigravity App/IDE, Claude Code, and Cursor skill destinations and asks before copying `tinta-designer` into them. Shared destinations are handled once. Updates preserve additional files, and unchanged skills are left as they are. The skill provides workflows for batch element creation, non-destructive canvas edits, and visual verification.

For manual installation or other agents, download `tinta-designer-skill.zip` from the [latest release](https://github.com/quenazsoares/tinta-mcp/releases/latest) and extract the `tinta-designer` directory into your agent's skills folder.

## Updating

To fetch the latest published version and update the Inkscape extension, run:

```bash
uvx tinta-mcp@latest install
```

Restart Inkscape afterward. Set the client's `args` to `["tinta-mcp@latest"]` and restart its MCP server to use the latest server version too. Without `@latest`, uvx can reuse a cached version.

To keep the server and extension on a specific release, use the same `tinta-mcp@X.Y.Z` version in the install command and client configuration, replacing `X.Y.Z` with the release number. Updating the server alone does not update the files already copied into Inkscape.

## How it works

Inkscape exposes internal actions over D-Bus. When Tinta triggers an action, Inkscape runs the extension in its own process. The extension can then read and update the document DOM.

The server and extension exchange requests through a private IPC directory for the current user. The server writes one versioned request, triggers `org.tinta.mcp` with `gdbus`, and accepts a response only when its session and request IDs match. The extension serializes the staged SVG before writing that response. If activation times out or no response arrives, Tinta reports that the outcome is unknown and leaves the request pending. It will not reuse the shared request slot until the pending request is resolved.

On Windows, Tinta uses the `gdbus.exe` bundled with Inkscape and does not require a separate D-Bus daemon. On Linux, it uses the user session bus.

## Available tools

### Element creation

- `create_elements`: Creates multiple SVG elements in a single call. Supports pipeline options to clear the document, set canvas dimensions, fit the viewBox with padding, select the created items, and return a PNG preview immediately.
- `create_shape`: Inserts basic shapes (`rect`, `circle`, `ellipse`, `line`, `polygon`, `polyline`).
- `create_path`: Inserts a `<path>` element using SVG path data (`d="..."`).
- `create_text`: Inserts text with position, font size, and style attributes.
- `create_group`: Creates a `<g>` group container. Existing children must be contiguous siblings; the group stays at their original paint position. Documents with CSS stylesheets are rejected for this operation.

### Document editing

- `modify_elements`: Modifies attributes of one or more existing SVG elements by ID in an atomic batch. Each entry needs an ID and at least one attribute, with optional auto-framing (`fit_canvas`) and preview (`return_preview`).
- `delete_elements`: Removes one or more existing elements by ID in an atomic batch. Pass a one-item list to delete one element.
- `clear_canvas`: Removes drawing elements while keeping definitions, Inkscape named view, metadata, title, description, and CSS style elements.

### Canvas and viewport

- `fit_canvas_to_elements`: Calculates the bounding box of existing elements and fits the page dimensions and viewBox around them with padding.
- `set_canvas_size`: Sets explicit width, height, and viewBox values. Supported units are px, mm, cm, in, pt, and pc.

### Inspection and export

- `get_document_info`: Returns document dimensions, units, viewBox when present, and element counts as JSON.
- `list_elements`: Returns a page of element summaries in document order, including nodes in definitions. The default page size is 100 and the maximum is 500.
- `get_element_info`: Returns all attributes and parsed inline style for one element ID.
- `get_selection`: Returns IDs and attributes of every selected element as JSON.
- `export_preview`: Renders the active page to a PNG through Inkscape's native actions, capped at the requested maximum pixel dimension (default 800, maximum 4096).

### Additional structured operations

- `apply_mask`: Applies or removes a clipping path or luminance mask. Applying a mask moves the stencil into definitions; transformed targets or stencils, inherited or class-based styles, and documents with CSS stylesheets are rejected.
- `path_boolean_operation`: Performs native path boolean operations on existing paths. All operations except combine and break-apart need two or more paths; those two accept one or more. Difference follows Inkscape's canvas paint order; the ID list does not define subtraction order.

A tool that returns a preview includes both the operation result and PNG in its MCP response. If preview export fails after an edit, the response keeps the edit result and adds a warning. Failed operations return MCP tool errors.

SVG tags and attributes are treated as data. Fixed extension handlers make document changes; the server never evaluates Python supplied by a client.

## Development

Clone the repository and install the development dependencies:

```bash
git clone https://github.com/quenazsoares/tinta-mcp.git
cd tinta-mcp
uv sync --all-groups
```

To install the extension from the checkout, run `install_windows.bat` on Windows or `bash scripts/install_linux.sh` on Linux. These scripts also set up the local environment and save `mcp_config.snippet.json` in the project root. The Windows PowerShell script can also be run directly with `powershell -ExecutionPolicy Bypass -File scripts/install_windows.ps1`.

Run the local server with `uv run tinta`. For MCP clients, use `"command": "uv"` and `"args": ["--directory", "/absolute/path/to/tinta-mcp", "run", "tinta"]`, replacing the path with your checkout directory. After changing extension code, run the installer again and restart Inkscape.

Run the test suite:

```bash
uv run pytest
```

Live tests are opt-in. Each test starts its own Inkscape process with a separate profile, IPC namespace, and disposable document. Tests do not use the user's open document:

```bash
uv run pytest tests/test_dbus_smoke.py --live-inkscape -v
```

On Linux without a display or session bus, prefix the command with `xvfb-run -a dbus-run-session`. When live tests are requested, a missing executable or readiness failure causes the test to fail. Each test owns its fixture and can run independently. Set `TINTA_TEST_INKSCAPE` to choose an Inkscape executable. For isolated extension tests, set `TINTA_TEST_INKSCAPE_PYTHON` and `TINTA_INKEX_PATH` to locate the Python interpreter and inkex package without installing them in the server environment.

Check formatting, lints, and types:

```bash
uv run ruff check extension tintamcp tests
uv run pyright
```

For version preparation, TestPyPI validation, and PyPI publication, see the [release guide](https://github.com/quenazsoares/tinta-mcp/blob/main/docs/releasing.md).

## Acknowledgements and lineage

Tinta builds upon earlier community work:

- [Shriinivas/inkmcp](https://github.com/Shriinivas/inkmcp): The original Inkscape MCP server prototype for Linux using D-Bus.
- [dev-willbird1936/inkmcp-windows](https://github.com/dev-willbird1936/inkmcp-windows): Added Windows support by locating the bundled `gdbus.exe`.

Tinta adds typed FastMCP tools, batch element operations, previews exported through GTK actions, input sanitization, and user-isolated temporary IPC directories.
