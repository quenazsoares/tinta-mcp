"""Live integration requires --live-inkscape and owns disposable documents."""

import base64
import struct

import pytest
from mcp.types import ImageContent, TextContent

from tintamcp import server

pytestmark = pytest.mark.live


def create_pair():
    return server.create_elements(
        [
            server.ElementSpec.model_validate(
                {"tag": "path", "id": "a", "d": "M 10,10 H 90 V 60 H 10 Z", "fill": "blue"}
            ),
            server.ElementSpec.model_validate(
                {"tag": "path", "id": "b", "d": "M 50,20 H 110 V 70 H 50 Z", "fill": "red"}
            ),
        ]
    )


def test_document_info(live_document):
    assert "Document information" in server.get_document_info()
    assert "fixture_anchor" in server.list_elements(limit=10)
    assert '"id": "fixture_anchor"' in server.get_element_info("fixture_anchor")


def test_failed_batch_does_not_clear_live_document(live_document):
    from fastmcp.exceptions import ToolError

    before = live_document.snapshot()
    with pytest.raises(ToolError, match="invalid name"):
        server.create_elements(
            [
                server.ElementSpec.model_validate(
                    {"tag": "rect", "id": "partial", "attributes": {"invalid name": "x"}}
                )
            ],
            clear_canvas=True,
        )
    after = live_document.snapshot()
    assert any(element.get("id") == "fixture_anchor" for element in after.iter())
    assert not any(element.get("id") == "partial" for element in after.iter())
    assert before.get("viewBox") == after.get("viewBox")


def test_creation_and_native_preview(live_document):
    result = server.create_elements(
        [
            server.ElementSpec.model_validate(
                {"tag": "rect", "id": "created", "width": 60, "height": 40}
            )
        ],
        return_preview=True,
    )
    assert isinstance(result, list)
    assert any(
        isinstance(item, TextContent) and "created successfully" in item.text for item in result
    )
    assert any(isinstance(item, ImageContent) and len(item.data) > 100 for item in result)
    assert any(element.get("id") == "created" for element in live_document.snapshot().iter())


def test_modify_elements(live_document):
    create_pair()
    assert "Updated" in str(
        server.modify_elements(
            elements=[server.ElementModification(id="a", attributes={"fill": "#f43f5e"})]
        )
    )
    element = next(
        element for element in live_document.snapshot().iter() if element.get("id") == "a"
    )
    assert element.get("fill") == "#f43f5e"


def test_apply_and_remove_mask(live_document):
    create_pair()
    assert "Applied" in str(server.apply_mask("a", "b"))
    root = live_document.snapshot()
    assert next(element for element in root.iter() if element.get("id") == "a").get("clip-path")
    container_id = (
        next(element for element in root.iter() if element.get("id") == "a")
        .get("clip-path")
        .removeprefix("url(#")
        .removesuffix(")")
    )
    container = next(element for element in root.iter() if element.get("id") == container_id)
    assert container.get("clipPathUnits") == "userSpaceOnUse"
    assert "Removed" in str(server.apply_mask("a", remove=True))
    root = live_document.snapshot()
    assert (
        next(element for element in root.iter() if element.get("id") == "a").get("clip-path")
        is None
    )


def test_boolean_union(live_document):
    create_pair()
    assert "executed" in str(server.path_boolean_operation("union", ["a", "b"]))
    root = live_document.snapshot()
    assert any(element.tag.endswith("}path") for element in root.iter())
    assert not any(element.get("id") == "b" for element in root.iter())


def test_boolean_difference_reports_paint_order_and_applies_result(live_document):
    create_pair()
    result = server.path_boolean_operation("difference", ["a", "b"])
    assert "canvas paint order" in str(result)
    root = live_document.snapshot()
    paths = [element for element in root.iter() if element.tag.endswith("}path")]
    assert paths
    assert not any(element.get("id") == "b" for element in root.iter())
    assert any(element.get("d") and "50" in element.get("d") for element in paths)


def test_boolean_missing_id_is_rejected_before_selection_or_mutation(live_document):
    from fastmcp.exceptions import ToolError

    create_pair()
    before = live_document.snapshot()
    with pytest.raises(ToolError, match="missing"):
        server.path_boolean_operation("union", ["a", "missing"])
    after = live_document.snapshot()
    assert [element.get("d") for element in before.iter() if element.tag.endswith("}path")]
    assert [element.get("d") for element in before.iter() if element.tag.endswith("}path")] == [
        element.get("d") for element in after.iter() if element.tag.endswith("}path")
    ]


def test_all_native_boolean_actions_are_available_for_paths(live_document):
    operations = ("union", "difference", "intersection", "exclusion", "division", "combine")
    for index, operation in enumerate(operations):
        first, second = f"{operation}_{index}_a", f"{operation}_{index}_b"
        result = server.create_elements(
            [
                server.ElementSpec.model_validate(
                    {"tag": "path", "id": first, "d": "M 10,10 H 60 V 60 H 10 Z"}
                ),
                server.ElementSpec.model_validate(
                    {"tag": "path", "id": second, "d": "M 30,30 H 80 V 80 H 30 Z"}
                ),
            ]
        )
        assert first in str(result) and second in str(result)
        assert "executed" in str(server.path_boolean_operation(operation, [first, second]))

    compound_id = "break_apart_compound"
    server.create_path(
        compound_id,
        "M 10,10 H 20 V 20 H 10 Z M 30,30 H 40 V 40 H 30 Z",
    )
    assert "executed" in str(server.path_boolean_operation("break-apart", [compound_id]))
    root = live_document.snapshot()
    assert any(element.tag.endswith("}path") for element in root.iter())


def test_export_preview(live_document):
    result = server.export_preview(max_size=128)
    assert isinstance(result, ImageContent) and result.mime_type == "image/png"
    image = base64.b64decode(result.data)
    assert image[:8] == b"\x89PNG\r\n\x1a\n"
    width, height = struct.unpack(">II", image[16:24])
    assert max(width, height) <= 128


def test_group_and_delete(live_document):
    create_pair()
    assert "created" in server.create_group("group", ["a", "b"])
    root = live_document.snapshot()
    group = next(element for element in root.iter() if element.get("id") == "group")
    assert [element.get("id") for element in group] == ["a", "b"]
    group_parent = next(parent for parent in root.iter() if group in list(parent))
    siblings = [element.get("id") for element in group_parent]
    assert siblings.index("group") == siblings.index("fixture_anchor") + 1
    assert "Deleted" in str(server.delete_elements(["group"]))
    assert not any(
        element.get("id") in {"group", "a", "b"} for element in live_document.snapshot().iter()
    )


def test_mcp_stdio_uses_structured_handlers(live_document):
    import asyncio
    import os
    import sys
    from pathlib import Path

    from fastmcp import Client
    from fastmcp.client.transports import PythonStdioTransport

    async def exercise():
        transport = PythonStdioTransport(
            Path(__file__).with_name("mcp_worker.py"),
            python_cmd=sys.executable,
            env={**os.environ, "TINTA_TEST_SERVICE": live_document.connection.dbus_service},
        )
        async with Client(transport, timeout=30) as client:
            names = {tool.name for tool in await client.list_tools()}
            assert (
                len(names) == 17
                and not {
                    "execute_python",
                    "inkscape_operation",
                    "modify_element",
                    "delete_element",
                }
                & names
            )
            assert {"list_elements", "get_element_info"} <= names
            removed = await client.call_tool(
                "execute_python", {"code": "print('NO_EXECUTION')"}, raise_on_error=False
            )
            assert removed.is_error
            rejected = await client.call_tool(
                "create_shape",
                {
                    "element_type": "execute-code",
                    "id": "probe",
                    "attributes": {"code": "print('NO_EXECUTION')"},
                },
                raise_on_error=False,
            )
            assert rejected.is_error
            assert not any(
                element.get("id") == "probe" for element in live_document.snapshot().iter()
            )
            await client.call_tool(
                "create_text", {"id": "label", "text": "Hello", "x": 10, "y": 20}
            )
            literal = "Text {__import__('builtins').print('NO_EXECUTION')} with 'quotes' and 雲"
            await client.call_tool(
                "modify_elements",
                {"elements": [{"id": "label", "attributes": {"text": literal}}]},
            )
            await client.call_tool("create_group", {"id": "group", "children_ids": ["label"]})
            root = live_document.snapshot()
            label = next(element for element in root.iter() if element.get("id") == "label")
            assert label.text == literal
            await client.call_tool("set_canvas_size", {"width": 320, "height": 200})
            await client.call_tool("fit_canvas_to_elements", {"padding": 10})
            await client.call_tool("delete_elements", {"ids": ["group"]})
            await client.call_tool("clear_canvas", {})
            assert not any(
                element.get("id") in {"group", "label", "fixture_anchor"}
                for element in live_document.snapshot().iter()
            )

    asyncio.run(exercise())
