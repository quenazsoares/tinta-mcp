"""Run with Inkscape's Python, never against a live document."""

# pyright: reportMissingImports=false
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from lxml import etree

sys.path.insert(0, os.environ["TINTA_INKEX_PATH"])
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "extension"))
import inkex  # noqa: E402
from tinta_mcp import ElementCreator  # noqa: E402


class Operations(unittest.TestCase):
    def setUp(self):
        self.svg = inkex.load_svg(
            b'<svg xmlns="http://www.w3.org/2000/svg" width="100" height="100"><rect id="r" x="10" y="20" width="60" height="30"/><rect id="m" width="20" height="20"/></svg>'
        ).getroot()
        self.extension = ElementCreator()
        self.extension.svg = self.svg
        self.extension.document = self.svg.getroottree()

    def operation(self, name, attributes):
        self.assertTrue(
            hasattr(self.extension, "handle_operation"),
            "Structured operation dispatcher is missing",
        )
        return self.extension.handle_operation(self.svg, name, attributes)

    def test_legacy_code_is_rejected(self):
        result = self.operation("execute-code", {"code": "svg.set('executed', 'yes')"})
        self.assertEqual(result["status"], "error")
        self.assertIsNone(self.svg.get("executed"))

    def test_legacy_batch_envelope_is_rejected_before_clear(self):
        with tempfile.TemporaryDirectory(prefix="tinta-unit-") as folder:
            response = Path(folder) / "response.json"
            (Path(folder) / "mcp_params.json").write_text(
                json.dumps(
                    {
                        "protocol_version": 1,
                        "session_id": "a" * 32,
                        "request_id": "b" * 32,
                        "operation": "create-elements",
                        "clear_canvas": True,
                        "elements": [],
                        "response_file": str(response),
                    }
                )
            )
            with patch("tinta_mcp.get_tinta_ipc_dir", return_value=Path(folder)):
                self.extension.effect()
            self.assertFalse(response.exists(), "client-supplied response paths must be ignored")
            self.assertIsNotNone(self.svg.getElementById("r"))

    def test_modify_keeps_expressions_literal(self):
        value = "literal {__import__('builtins').print('INJECTED')} ' 雲"
        result = self.operation(
            "modify-elements",
            {"elements": [{"id": "r", "attributes": {"text": value, "fill": "red"}}]},
        )
        self.assertEqual(result["status"], "success")
        self.assertEqual(self.svg.getElementById("r").text, value)
        self.assertEqual(self.svg.getElementById("r").get("fill"), "red")

    def test_modify_rejects_missing_ids_before_applying_any_change(self):
        result = self.operation(
            "modify-elements",
            {
                "elements": [
                    {"id": "r", "attributes": {"fill": "green"}},
                    {"id": "missing", "attributes": {"fill": "blue"}},
                ]
            },
        )
        self.assertEqual(result["status"], "error")
        self.assertIsNone(self.svg.getElementById("r").get("fill"))

    def test_creation_rejects_an_existing_id(self):
        with self.assertRaises(ValueError):
            self.extension.create_element_recursive(
                self.svg, {"tag": "rect", "attributes": {"id": "r"}}
            )

    def test_canvas_rejects_non_finite_dimensions(self):
        before = (self.svg.get("width"), self.svg.get("height"), self.svg.get("viewBox"))
        result = self.operation("set-canvas", {"width": float("nan"), "height": 20})
        self.assertEqual(result["status"], "error")
        self.assertEqual(
            (self.svg.get("width"), self.svg.get("height"), self.svg.get("viewBox")), before
        )

    def test_modify_cannot_change_an_element_id(self):
        result = self.operation(
            "modify-elements", {"elements": [{"id": "r", "attributes": {"id": "renamed"}}]}
        )
        self.assertEqual(result["status"], "error")
        self.assertIsNotNone(self.svg.getElementById("r"))
        self.assertIsNone(self.svg.getElementById("renamed"))

    def test_creation_rejects_missing_local_reference(self):
        with self.assertRaisesRegex(ValueError, "Referenced ID"):
            self.extension._prepare_ids(
                self.svg,
                [{"tag": "rect", "attributes": {"id": "new", "fill": "url(#missing)"}}],
            )

    def test_creation_allows_forward_reference_to_another_batch_element(self):
        self.extension._prepare_ids(
            self.svg,
            [
                {"tag": "use", "attributes": {"id": "copy", "href": "#new"}},
                {"tag": "rect", "attributes": {"id": "new"}},
            ],
        )

    def test_modify_rejects_missing_local_reference(self):
        result = self.operation(
            "modify-elements",
            {"elements": [{"id": "r", "attributes": {"fill": "url(#missing)"}}]},
        )
        self.assertEqual(result["status"], "error")
        self.assertIsNone(self.svg.getElementById("r").get("fill"))

    def test_modify_rejects_empty_or_malformed_batch(self):
        for elements in ([], [{"attributes": {"fill": "red"}}], [{"id": "r"}]):
            with self.subTest(elements=elements):
                before = etree.tostring(self.svg)
                result = self.operation("modify-elements", {"elements": elements})
                self.assertEqual(result["status"], "error")
                self.assertEqual(etree.tostring(self.svg), before)

    def test_delete_rejects_still_referenced_ids(self):
        self.svg.getElementById("m").set("fill", "url(#r)")
        result = self.operation("delete-elements", {"ids": ["r"]})
        self.assertEqual(result["status"], "error")
        self.assertIsNotNone(self.svg.getElementById("r"))

    def test_delete_rejects_missing_id_before_removing_existing(self):
        result = self.operation("delete-elements", {"ids": ["r", "missing"]})
        self.assertEqual(result["status"], "error")
        self.assertIsNotNone(self.svg.getElementById("r"))

    def test_delete_rejects_empty_id_list(self):
        before = etree.tostring(self.svg)
        result = self.operation("delete-elements", {"ids": []})
        self.assertEqual(result["status"], "error")
        self.assertEqual(etree.tostring(self.svg), before)

    def test_failed_batch_keeps_original_document_and_correlates_error(self):
        with tempfile.TemporaryDirectory(prefix="tinta-stage-") as folder:
            folder_path = Path(folder)
            request = {
                "protocol_version": 1,
                "session_id": "a" * 32,
                "request_id": "b" * 32,
                "request": {
                    "operation": "create-elements",
                    "clear_canvas": True,
                    "elements": [{"tag": "rect", "attributes": {"invalid name": "x"}}],
                },
            }
            (folder_path / "mcp_params.json").write_text(json.dumps(request), encoding="utf-8")
            original = self.extension.svg
            with patch("tinta_mcp.get_tinta_ipc_dir", return_value=folder_path):
                self.extension.effect()
                self.assertIs(self.extension.svg, original)
                self.assertIsNotNone(self.extension.svg.getElementById("r"))
                with patch.object(inkex.EffectExtension, "save_raw") as save_raw:
                    self.extension.save_raw(None)
                save_raw.assert_called_once_with(None)

            response_path = folder_path / f"mcp_response_{'b' * 32}.json"
            response = json.loads(response_path.read_text(encoding="utf-8"))
            self.assertEqual(response["status"], "error")
            self.assertEqual(response["request_id"], "b" * 32)
            self.assertFalse((folder_path / "mcp_processing.json").exists())

    def test_group_and_delete(self):
        self.assertEqual(
            self.operation("create-group", {"id": "g", "children_ids": ["r"]})["status"], "success"
        )
        self.assertEqual(self.svg.getElementById("r").getparent().get("id"), "g")
        self.assertEqual(self.operation("delete-elements", {"ids": ["g"]})["status"], "success")
        self.assertIsNone(self.svg.getElementById("g"))

    def test_group_preserves_parent_and_sibling_paint_order(self):
        self.svg = inkex.load_svg(
            b'<svg xmlns="http://www.w3.org/2000/svg"><g id="parent">'
            b'<rect id="a"/><rect id="b"/><rect id="c"/></g></svg>'
        ).getroot()
        result = self.operation("create-group", {"id": "group", "children_ids": ["a", "b"]})
        parent = self.svg.getElementById("parent")
        self.assertEqual(result["status"], "success")
        self.assertEqual([child.get("id") for child in parent], ["group", "c"])
        self.assertEqual(
            [child.get("id") for child in self.svg.getElementById("group")], ["a", "b"]
        )

    def test_group_rejects_noncontiguous_siblings_without_mutation(self):
        self.svg = inkex.load_svg(
            b'<svg xmlns="http://www.w3.org/2000/svg"><rect id="a"/>'
            b'<circle id="middle"/><rect id="b"/></svg>'
        ).getroot()
        before = etree.tostring(self.svg)
        result = self.operation("create-group", {"id": "group", "children_ids": ["a", "b"]})
        self.assertEqual(result["status"], "error")
        self.assertEqual(etree.tostring(self.svg), before)
        self.assertIsNone(self.svg.getElementById("group"))

    def test_mask_rejects_transformed_stencil_without_mutation(self):
        self.svg.getElementById("m").set("transform", "translate(20, 0)")
        before = etree.tostring(self.svg)
        result = self.operation("apply-mask", {"target_id": "r", "mask_id": "m", "mode": "clip"})
        self.assertEqual(result["status"], "error")
        self.assertEqual(etree.tostring(self.svg), before)

    def test_mask_rejects_ancestor_relationship_without_mutation(self):
        self.svg = inkex.load_svg(
            b'<svg xmlns="http://www.w3.org/2000/svg"><g id="stencil"><rect id="target"/></g></svg>'
        ).getroot()
        before = etree.tostring(self.svg)
        result = self.operation(
            "apply-mask", {"target_id": "target", "mask_id": "stencil", "mode": "clip"}
        )
        self.assertEqual(result["status"], "error")
        self.assertEqual(etree.tostring(self.svg), before)

    def test_mask_rejects_external_stylesheet_without_mutation(self):
        self.svg = inkex.load_svg(
            b'<?xml-stylesheet type="text/css" href="theme.css"?>'
            b'<svg xmlns="http://www.w3.org/2000/svg"><rect id="target"/>'
            b'<rect id="stencil"/></svg>'
        ).getroot()
        before = etree.tostring(self.svg.getroottree())
        result = self.operation(
            "apply-mask", {"target_id": "target", "mask_id": "stencil", "mode": "clip"}
        )
        self.assertEqual(result["status"], "error")
        self.assertEqual(etree.tostring(self.svg.getroottree()), before)

    def test_mask_alias_is_rejected_without_mutation(self):
        before = etree.tostring(self.svg)
        result = self.operation("apply-mask", {"target_id": "r", "mask_id": "m", "mode": "mask"})
        self.assertEqual(result["status"], "error")
        self.assertEqual(etree.tostring(self.svg), before)

    def test_document_info_ignores_comments_and_reports_units(self):
        svg = inkex.load_svg(
            b'<svg xmlns="http://www.w3.org/2000/svg" width="210mm" height="297mm" '
            b'viewBox="0 0 800 1100"><!-- comment --><rect id="r"/></svg>'
        ).getroot()
        result = self.extension.get_document_info(svg)
        self.assertEqual(result["status"], "success")
        data = result["data"]
        self.assertEqual(data["dimensions"], {"width": "210mm", "height": "297mm"})
        self.assertEqual(data["viewBox"], ["0", "0", "800", "1100"])
        self.assertEqual(data["elementCounts"]["rect"], 1)

    def test_list_elements_returns_bounded_document_order_summaries(self):
        self.svg = inkex.load_svg(
            b'<svg xmlns="http://www.w3.org/2000/svg" xmlns:inkscape="http://www.inkscape.org/namespaces/inkscape">'
            b'<defs><linearGradient id="paint"/></defs><g id="group" inkscape:label="Layer">'
            b'<rect id="first"/><circle id="second"/></g></svg>'
        ).getroot()
        first_page = self.operation("list-elements", {"limit": 2, "offset": 0})["data"]
        second_page = self.operation("list-elements", {"limit": 2, "offset": 2})["data"]
        third_page = self.operation("list-elements", {"limit": 2, "offset": 4})["data"]
        self.assertEqual(first_page["total"], 5)
        self.assertEqual(first_page["elements"][1]["id"], "paint")
        self.assertEqual(second_page["elements"][0]["id"], "group")
        self.assertEqual(second_page["elements"][0]["label"], "Layer")
        self.assertEqual(third_page["elements"][0]["id"], "second")
        self.assertEqual(third_page["elements"][0]["parent_id"], "group")

    def test_element_info_returns_attributes_for_public_inspection(self):
        result = self.operation("get-info-by-id", {"id": "r"})
        self.assertEqual(result["status"], "success")
        self.assertEqual(result["data"]["attributes"]["width"], "60")

    def test_mask_and_remove(self):
        self.assertEqual(
            self.operation(
                "apply-mask", {"target_id": "r", "mask_id": "m", "mode": "clip", "remove": False}
            )["status"],
            "success",
        )
        self.assertTrue(self.svg.getElementById("r").get("clip-path").startswith("url(#"))
        self.assertEqual(
            self.operation(
                "apply-mask", {"target_id": "r", "mask_id": None, "mode": "clip", "remove": True}
            )["status"],
            "success",
        )
        self.assertIsNone(self.svg.getElementById("r").get("clip-path"))

    def test_canvas_handlers(self):
        self.assertEqual(
            self.operation("set-canvas", {"width": 320, "height": 200, "unit": "px"})["status"],
            "success",
        )
        self.assertEqual(self.svg.get("viewBox"), "0 0 320.0 200.0")
        self.assertEqual(self.operation("fit-canvas", {"padding": 5})["status"], "success")
        self.assertEqual(self.operation("clear-canvas", {})["status"], "success")
        self.assertIsNone(self.svg.getElementById("r"))

    def test_clear_canvas_preserves_document_metadata_style_and_defs(self):
        self.svg = inkex.load_svg(
            b'<svg xmlns="http://www.w3.org/2000/svg"><title>Artwork</title>'
            b"<desc>Document description</desc><style>.shape { fill: red; }</style>"
            b'<defs><linearGradient id="paint"/></defs><rect id="shape" class="shape"/></svg>'
        ).getroot()
        result = self.operation("clear-canvas", {})
        self.assertEqual(result["status"], "success")
        self.assertIsNotNone(self.svg.find("{http://www.w3.org/2000/svg}title"))
        self.assertIsNotNone(self.svg.find("{http://www.w3.org/2000/svg}desc"))
        self.assertIsNotNone(self.svg.find("{http://www.w3.org/2000/svg}style"))
        self.assertIsNotNone(self.svg.getElementById("paint"))
        self.assertIsNone(self.svg.getElementById("shape"))

    def test_id_expression_is_not_executed(self):
        result = self.operation(
            "create-group",
            {"id": "g{__import__('builtins').print('INJECTED')}", "children_ids": []},
        )
        self.assertEqual(result["status"], "error")
        self.assertEqual(len(self.svg.xpath("//svg:g")), 0)

    def test_creation_text_is_data(self):
        value = "A < B & {literal}"
        element = self.extension.create_element_recursive(
            self.svg, {"tag": "text", "attributes": {"id": "label", "text": value}}
        )
        self.assertEqual(element.text, value)
        self.assertEqual(element.get("id"), "label")


if __name__ == "__main__":
    unittest.main()
