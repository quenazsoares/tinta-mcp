# Tinta

![Tinta — Inkscape × AI × MCP. Give your AI a vector editor.](https://raw.githubusercontent.com/quenazsoares/tinta-mcp/main/assets/tinta-mcp-banner.png)

[![Python 3.12+](https://img.shields.io/badge/python-3.12+-blue.svg)](https://www.python.org/downloads/)
[![License: AGPL v3](https://img.shields.io/badge/License-AGPL_v3-blue.svg)](https://www.gnu.org/licenses/agpl-3.0)
[![FastMCP 4.0](https://img.shields.io/badge/FastMCP-4.0-green.svg)](https://github.com/jlowin/fastmcp)

Tinta (*ink* in Portuguese) connects AI assistants to an open Inkscape session through MCP. Its structured tools create and edit SVG elements, inspect documents, and export previews using Inkscape.

## How it works

Inkscape exposes internal actions over D-Bus. When Tinta triggers an action, Inkscape runs the extension in its own process. The extension can then read and update the document DOM.

The server and extension exchange requests through a private IPC directory for the current user. The server writes one versioned request, triggers `org.tinta.mcp` with `gdbus`, and accepts a response only when its session and request IDs match. The extension serializes the staged SVG before writing that response. If activation times out or no response arrives, Tinta reports that the outcome is unknown and leaves the request pending. It will not reuse the shared request slot until the pending request is resolved.

On Windows, Tinta uses the `gdbus.exe` bundled with Inkscape and does not require a separate D-Bus daemon. On Linux, it uses the user session bus.

## Requirements

- Inkscape 1.2 or newer
- Python 3.12 or newer
- [uv](https://docs.astral.sh/uv/)

## Installation

### Windows

Run the installer batch script:

```cmd
install_windows.bat
```

Or run the PowerShell script directly:

```powershell
powershell -ExecutionPolicy Bypass -File scripts/install_windows.ps1
```

The installer locates Inkscape and copies the extension and its `tinta_mcp_ops` package to the user extensions directory. It then runs `uv sync` and prints the MCP client configuration. If `preferences.xml` does not exist yet, launch and close Inkscape, then run the installer again to suppress the first-run welcome dialog.

### Linux and WSL

Run the Linux installer script from the repository root:

```bash
bash scripts/install_linux.sh
```

The installer checks for `inkscape` and `gdbus`, then copies the extension and its `tinta_mcp_ops` package to `~/.config/inkscape/extensions`. It runs `uv sync` and writes `mcp_config.snippet.json` with the project directory filled in.

## Client configuration

Both installers print a configuration block and save it to `mcp_config.snippet.json` in the project root. Copy the entry into your client configuration file, such as `claude_desktop_config.json` or `mcp_config.json`:

```json
{
  "mcpServers": {
    "tinta": {
      "command": "uv",
      "args": [
        "--directory",
        "/absolute/path/to/tinta",
        "run",
        "tinta"
      ]
    }
  }
}
```

Tinta provides structured SVG operations. It does not run arbitrary Python or textual commands, and the `--enable-python` argument is no longer accepted.

When upgrading from 0.2.1, update both the server and extension, then restart Inkscape. Editing the source checkout does not update an installed extension. Remove `--enable-python` from the client configuration.

### AI assistant skill

For AI environments that support skills (such as Claude Code, Cursor, or Antigravity), download `tinta-designer-skill.zip` from the latest release and extract the `tinta-designer` directory into your agent's skills folder. The skill equips assistants with workflows for batch element creation, non-destructive canvas edits, and visual verification.

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

## Acknowledgements and lineage

Tinta builds upon earlier community work:

- [Shriinivas/inkmcp](https://github.com/Shriinivas/inkmcp): The original Inkscape MCP server prototype for Linux using D-Bus.
- [dev-willbird1936/inkmcp-windows](https://github.com/dev-willbird1936/inkmcp-windows): Added Windows support by locating the bundled `gdbus.exe`.

Tinta adds typed FastMCP tools, batch element operations, previews exported through GTK actions, input sanitization, and user-isolated temporary IPC directories.
