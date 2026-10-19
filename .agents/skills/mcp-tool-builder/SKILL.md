---
name: mcp-tool-builder
description: "Step-by-step guide for creating, modifying, and testing MCP tools for Inkscape in Tinta."
---

# MCP tool builder for Tinta

Use this skill when adding a new tool or modifying an existing tool in Tinta.

## Dual-runtime architecture

Every tool in Tinta bridges two distinct environments:

1. Host server (`tintamcp/`): Runs in external Python with FastMCP. Defines the tool signature, validates types, atomically writes a versioned request to the user's IPC directory, and calls D-Bus.
2. Inkscape extension (`extension/`): Runs inside Inkscape's Python context via `inkex`. Claims and validates the IPC request, stages document edits, serializes the SVG, and atomically writes a correlated response.

```
[Client / LLM] â”€â”€> FastMCP (@mcp.tool) â”€â”€> IPC Request File â”€â”€> gdbus â”€â”€> [Inkscape Extension] â”€â”€> SVG DOM
```

## Step 1: Define the tool in `tintamcp/server.py`

Register the tool on the `mcp` instance using clear, type-annotated parameters and concise docstrings designed for LLM consumption:

```python
@mcp.tool()
def my_new_tool(element_id: str, radius: float = 10.0, color: str = "#000000") -> str:
    """Short description of what the tool accomplishes on the canvas.

    Args:
        element_id: Unique identifier for the SVG element.
        radius: Radius in user units (default 10.0).
        color: Hex or CSS color string (e.g. '#ff0000').
    """
    payload = {
        "operation": "my-operation",
        "attributes": {
            "id": element_id,
            "radius": str(radius),
            "color": color,
        },
    }
    return str(dispatch_operation(payload))
```

### Design guidelines for MCP tools

- Use snake_case for Python parameters. Tinta automatically normalizes snake_case to kebab-case SVG attributes (`stroke_width` -> `stroke-width`).
- Provide sensible defaults to reduce round-trips for common actions.
- Prefer atomic operations. When an operation involves creating elements, fitting canvas, or generating previews, combine them into a single round-trip (see `create_elements`).

### Docstring design for LLM consumers

Tool docstrings are injected directly into the client LLM system prompt. Follow these guidelines to maximize tool calling accuracy and minimize token overhead:
- State actions directly using imperative mood in the first line (`"Create an SVG rectangle."`, not `"This tool allows the user to seamlessly create..."`).
- Avoid marketing filler and buzzwords (*robust*, *seamless*, *powerful*, *effortless*, *cutting-edge*, *comprehensive*).
- Document units and formats explicitly (pixels, percentages, hex colors) and valid ranges in the `Args:` section.
- Explain non-obvious defaults concisely so the LLM can plan its workflow in fewer calls.

## Step 2: Implement the operation in `extension/`

Operations live under `extension/tinta_mcp_ops/`:
- Shape and element creation: `extension/tinta_mcp_ops/element_mapping.py`
- Manipulation and attributes: `extension/tinta_mcp_ops/document_operations.py`

### Handling the action in `extension/tinta_mcp.py`

Register a fixed handler in `DOCUMENT_OPERATIONS`, called by `ElementCreator.handle_operation`. Never generate Python source or expose an interpreter. Operation names are independent of SVG tags. For queries handled by the extension:

```python
if operation == "my-operation":
    # 1. Access the open document DOM via self.svg
    elem = self.svg.getElementById(attributes.get("id"))

    # 2. Mutate DOM using inkex elements
    # 3. Return response data; the extension publishes it after SVG serialization.
```

The extension never accepts a response path from an operation. Requests use protocol version 1 with generated session and request IDs; responses use the corresponding derived path.

## Step 3: Test and debug

### Manual D-Bus trigger (Windows and Linux)

Verify that Inkscape responds over the session bus:

Windows (PowerShell):
```powershell
$gdbus = "C:\Program Files\Inkscape\bin\gdbus.exe"
& $gdbus call --session --dest org.inkscape.Inkscape --object-path /org/inkscape/Inkscape --method org.gtk.Actions.List | Select-String "org.tinta.mcp"
```

Linux / WSL (Bash):
```bash
gdbus call --session --dest org.inkscape.Inkscape --object-path /org/inkscape/Inkscape --method org.gtk.Actions.List | grep org.tinta.mcp
```

### Automated integration test

Add a test in [tests/test_dbus_smoke.py](../../../tests/test_dbus_smoke.py):

```python
def test_my_new_tool_live():
    result = my_new_tool(element_id="test_item_1", radius=15.0)
    assert "success" in result.lower()
```

Run tests locally:
```bash
uv run pytest
```

Use `uv run pytest tests/test_dbus_smoke.py --live-inkscape -v` for live tests. The fixture must own an isolated process/profile/document; never target the user's running session.
