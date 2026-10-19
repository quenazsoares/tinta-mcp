"""Regressions for the data-only MCP interface."""

import ast
import asyncio
import importlib.util
import sys
from pathlib import Path
from unittest.mock import patch

import pytest
from fastmcp import Client
from mcp.types import ImageContent, TextContent

from tintamcp import server


def test_removed_tools_are_not_discoverable():
    async def discover():
        with patch.object(
            server, "get_inkscape_connection", side_effect=RuntimeError("offline test")
        ):
            async with Client(server.mcp) as client:
                return {tool.name for tool in await client.list_tools()}

    names = asyncio.run(discover())
    assert "execute_python" not in names
    assert "inkscape_operation" not in names
    assert "modify_element" not in names
    assert "delete_element" not in names
    assert {"list_elements", "get_element_info"} <= names
    assert {"create_group", "modify_elements", "apply_mask", "clear_canvas"} <= names
    assert len(names) == 17


def test_mcp_schemas_expose_enums_and_canvas_units():
    async def discover():
        async with Client(server.mcp) as client:
            return {tool.name: tool for tool in await client.list_tools()}

    tools = asyncio.run(discover())
    schemas = {name: tool.input_schema for name, tool in tools.items()}
    assert schemas["create_shape"]["properties"]["element_type"]["enum"] == [
        "rect",
        "circle",
        "ellipse",
        "line",
        "polygon",
        "polyline",
    ]
    assert schemas["path_boolean_operation"]["properties"]["operation"]["enum"] == [
        "union",
        "difference",
        "intersection",
        "exclusion",
        "division",
        "combine",
        "break-apart",
    ]
    assert schemas["apply_mask"]["properties"]["mode"]["enum"] == ["clip", "luminance"]
    assert schemas["set_canvas_size"]["properties"]["unit"]["enum"] == [
        "px",
        "mm",
        "cm",
        "in",
        "pt",
        "pc",
    ]
    assert schemas["list_elements"]["properties"]["limit"]["minimum"] == 1
    assert schemas["list_elements"]["properties"]["limit"]["maximum"] == 500
    assert schemas["list_elements"]["properties"]["offset"]["minimum"] == 0
    assert schemas["export_preview"]["properties"]["max_size"]["minimum"] == 1
    assert schemas["export_preview"]["properties"]["max_size"]["maximum"] == 4096
    create_schema = schemas["create_elements"]
    canvas_schema = create_schema["properties"]["canvas_size"]
    if "anyOf" in canvas_schema:
        canvas_schema = next(item for item in canvas_schema["anyOf"] if "$ref" in item)
    if "$ref" in canvas_schema:
        canvas_schema = create_schema["$defs"][canvas_schema["$ref"].split("/")[-1]]
    assert {"width", "height", "unit"} <= canvas_schema["properties"].keys()
    assert canvas_schema["properties"]["unit"]["enum"] == ["px", "mm", "cm", "in", "pt", "pc"]
    assert "attributes" in (tools["create_elements"].description or "")
    element_schema = create_schema["properties"]["elements"]["items"]
    if "$ref" in element_schema:
        element_schema = create_schema["$defs"][element_schema["$ref"].split("/")[-1]]
    assert {"tag", "type", "id", "attributes", "children"} <= element_schema["properties"].keys()
    assert element_schema["additionalProperties"] is True
    modification = schemas["modify_elements"]["properties"]["elements"]["items"]
    if "$ref" in modification:
        modification = schemas["modify_elements"]["$defs"][modification["$ref"].split("/")[-1]]
    assert {"id", "attributes"} <= set(modification["required"])


def test_removed_flag_fails_before_starting_server():
    with (
        patch.object(sys, "argv", ["tinta", "--enable-python"]),
        patch.object(server.mcp, "run") as run,
    ):
        with pytest.raises(SystemExit) as error:
            server.main()
        assert error.value.code == 2
        run.assert_not_called()


def test_extension_errors_are_mcp_tool_errors():
    from fastmcp.exceptions import ToolError

    with (
        patch.object(server, "get_inkscape_connection") as get_connection,
        pytest.raises(ToolError, match="Element not found"),
    ):
        get_connection.return_value.execute_operation.return_value = {
            "status": "error",
            "data": {"error": "Element not found: missing"},
        }
        server.dispatch_operation({"operation": "delete-elements"})


def test_document_info_response_keeps_all_reported_metadata():
    data = {
        "message": "Document information",
        "dimensions": {"width": "210mm", "height": "297mm"},
        "units": {"width": "mm", "height": "mm"},
        "viewBox": ["0", "0", "800", "1100"],
        "elementCounts": {"svg": 1, "rect": 4},
    }
    response = server.format_response({"status": "success", "data": data})
    assert '"dimensions": {"height": "297mm", "width": "210mm"}' in response
    assert '"viewBox": ["0", "0", "800", "1100"]' in response
    assert '"elementCounts": {"rect": 4, "svg": 1}' in response


def test_failed_operation_is_reported_as_mcp_error():
    async def call_tool():
        with patch.object(server, "get_inkscape_connection") as get_connection:
            get_connection.return_value.execute_operation.return_value = {
                "status": "error",
                "data": {"error": "Element not found: missing"},
            }
            async with Client(server.mcp) as client:
                return await client.call_tool(
                    "delete_elements", {"ids": ["missing"]}, raise_on_error=False
                )

    result = asyncio.run(call_tool())
    assert result.is_error
    assert "Element not found" in getattr(result.content[0], "text", "")


def test_preview_failure_preserves_applied_operation_result():
    with (
        patch.object(
            server, "dispatch_operation", return_value="✅ 1 elements created"
        ) as dispatch,
        patch.object(server, "get_inkscape_connection") as get_connection,
    ):
        get_connection.return_value.export_native_preview.return_value = None
        result = server.create_elements(
            [server.ElementSpec(tag="rect", id="preview-test")], return_preview=True
        )
    dispatch.assert_called_once()
    assert "1 elements created" in str(result)
    assert "operation was applied" in str(result)


def test_invalid_preview_limit_fails_before_mutating_operation():
    from fastmcp.exceptions import ToolError

    with (
        patch.object(server, "dispatch_operation") as dispatch,
        pytest.raises(ToolError, match="from 1 to 4096"),
    ):
        server.create_elements(
            [server.ElementSpec(tag="rect", id="too-large")],
            return_preview=True,
            preview_max_size=5000,
        )
    dispatch.assert_not_called()


def test_preview_success_returns_operation_text_and_image_through_mcp():
    async def call_tool():
        with (
            patch.object(server, "dispatch_operation", return_value="✅ Created IDs: shape-1"),
            patch.object(server, "get_inkscape_connection") as get_connection,
        ):
            get_connection.return_value.is_available.return_value = True
            get_connection.return_value.export_native_preview.return_value = ImageContent(
                type="image", data="cG5n", mime_type="image/png"
            )
            async with Client(server.mcp) as client:
                return await client.call_tool(
                    "create_elements",
                    {"elements": [{"tag": "rect", "id": "shape-1"}], "return_preview": True},
                )

    result = asyncio.run(call_tool())
    assert not result.is_error
    assert any(isinstance(item, TextContent) and "shape-1" in item.text for item in result.content)
    assert any(isinstance(item, ImageContent) for item in result.content)


def test_boolean_operation_rejects_missing_path_before_native_actions():
    from fastmcp.exceptions import ToolError

    with patch.object(server, "get_inkscape_connection") as get_connection:
        connection = get_connection.return_value
        connection.is_available.return_value = True
        connection.execute_operation.return_value = {
            "status": "error",
            "data": {"error": "Element not found: missing"},
        }
        with pytest.raises(ToolError, match="Element not found"):
            server.path_boolean_operation("union", ["missing", "other"])
        connection.activate_action.assert_not_called()


@pytest.mark.parametrize("tag", ["execute-code", "get-info", "batch", "export-document-image"])
def test_shape_tag_cannot_select_an_operation(tag):
    from fastmcp.exceptions import ToolError

    with (
        patch.object(server, "dispatch_operation") as dispatch,
        pytest.raises(ToolError, match="Unsupported shape type"),
    ):
        server.create_shape(tag, "probe", {"code": "print('untrusted')"})
    dispatch.assert_not_called()


@pytest.mark.parametrize(
    ("call", "operation", "attributes"),
    [
        (
            lambda: server.create_group("g", ["r"]),
            "create-group",
            {"id": "g", "children_ids": ["r"]},
        ),
        (
            lambda: server.modify_elements(
                elements=[server.ElementModification(id="r", attributes={"stroke_width": 2})]
            ),
            "modify-elements",
            {"elements": [{"id": "r", "attributes": {"stroke-width": "2"}}]},
        ),
        (lambda: server.delete_elements(["r"]), "delete-elements", {"ids": ["r"]}),
        (
            lambda: server.apply_mask("r", "m"),
            "apply-mask",
            {"target_id": "r", "mask_id": "m", "mode": "clip", "remove": False},
        ),
        (lambda: server.fit_canvas_to_elements(5), "fit-canvas", {"padding": 5}),
        (
            lambda: server.set_canvas_size(320, 200),
            "set-canvas",
            {"width": 320, "height": 200, "unit": "px"},
        ),
        (lambda: server.clear_canvas(), "clear-canvas", {}),
    ],
)
def test_operations_send_data_instead_of_programs(call, operation, attributes):
    with patch.object(server, "dispatch_operation", return_value="✅ done") as dispatch:
        call()
    assert dispatch.call_args.args[0] == {"operation": operation, "attributes": attributes}


def test_inspection_tools_dispatch_bounded_queries():
    with patch.object(server, "dispatch_operation", return_value="data") as dispatch:
        assert server.list_elements(limit=20, offset=40) == "data"
        assert dispatch.call_args.args[0] == {
            "operation": "list-elements",
            "attributes": {"limit": 20, "offset": 40},
        }
        assert server.get_element_info("shape-1") == "data"
        assert dispatch.call_args.args[0] == {
            "operation": "get-info-by-id",
            "attributes": {"id": "shape-1"},
        }


@pytest.mark.parametrize("limit,offset", [(0, 0), (501, 0), (True, 0), (10, -1), (10, True)])
def test_list_elements_rejects_invalid_pagination(limit, offset):
    from fastmcp.exceptions import ToolError

    with patch.object(server, "dispatch_operation") as dispatch:
        with pytest.raises(ToolError):
            server.list_elements(limit=limit, offset=offset)
        dispatch.assert_not_called()


def test_element_tag_is_nested_under_creation_operation():
    with patch.object(server, "dispatch_operation", return_value="✅ done") as dispatch:
        server.create_text("label", "Text {literal} with 'quotes' and 雲", 10, 20)
    payload = dispatch.call_args.args[0]
    assert payload["operation"] == "create-element"
    assert payload["element"]["tag"] == "text"
    assert payload["element"]["attributes"]["text"] == "Text {literal} with 'quotes' and 雲"
    assert "tag" not in payload


def test_no_python_interpreter_in_production_sources():
    root = Path(__file__).resolve().parents[1]
    prohibited = []
    for folder in (root / "tintamcp", root / "extension"):
        for path in folder.rglob("*.py"):
            for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
                if (
                    isinstance(node, ast.Call)
                    and isinstance(node.func, ast.Name)
                    and node.func.id in {"exec", "eval", "compile"}
                ):
                    prohibited.append(f"{path.name}:{node.lineno}:{node.func.id}")
    assert prohibited == []


def test_extension_has_no_secondary_inkscape_cli_renderer():
    root = Path(__file__).resolve().parents[1] / "extension"
    sources = "\n".join(path.read_text(encoding="utf-8") for path in root.rglob("*.py"))
    assert "inkex.command" not in sources


def test_collecting_smoke_module_never_probes_running_session():
    path = Path(__file__).with_name("test_dbus_smoke.py")
    spec = importlib.util.spec_from_file_location("isolated_smoke_import", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    with patch.object(server, "get_inkscape_connection") as connection:
        spec.loader.exec_module(module)
    connection.assert_not_called()


def test_removed_flag_is_rejected_before_installation():
    import runpy

    with (
        patch.object(sys, "argv", ["tinta", "install", "--enable-python"]),
        patch("tintamcp.installer.run_installer") as installer,
    ):
        with pytest.raises(SystemExit) as error:
            runpy.run_module("tintamcp.main", run_name="__main__")
        assert error.value.code == 2
        installer.assert_not_called()
