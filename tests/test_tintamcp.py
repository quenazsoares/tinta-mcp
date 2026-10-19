"""Unit tests for structured tool payloads and utilities."""

import unittest


class TestTintaMCP(unittest.TestCase):
    def test_normalize_element_spec(self):
        from tintamcp.server import _normalize_element_spec

        raw = {
            "type": "rect",
            "id": "r1",
            "x": 10,
            "y": 20,
            "width": 100,
            "height": 50,
            "fill": "blue",
            "stroke_width": 2,
        }
        spec = _normalize_element_spec(raw)
        self.assertEqual(spec["tag"], "rect")
        self.assertEqual(spec["attributes"]["id"], "r1")
        self.assertEqual(spec["attributes"]["x"], "10")
        self.assertEqual(spec["attributes"]["width"], "100")
        self.assertEqual(spec["attributes"]["stroke-width"], "2")
        self.assertEqual(spec["attributes"]["fill"], "blue")

    def test_get_tinta_ipc_dir(self):
        from tintamcp.gdbus_util import get_tinta_ipc_dir

        ipc_dir = get_tinta_ipc_dir()
        self.assertTrue(ipc_dir.exists())
        self.assertTrue("tintamcp" in str(ipc_dir))

    def test_create_elements_pipeline_normalization(self):
        from tintamcp.server import _normalize_element_spec

        elem = {
            "tag": "circle",
            "id": "c1",
            "cx": 100,
            "cy": 100,
            "r": 50,
            "fill": "red",
        }
        normalized = _normalize_element_spec(elem)
        self.assertEqual(normalized["tag"], "circle")
        self.assertEqual(normalized["attributes"]["cx"], "100")
        self.assertEqual(normalized["attributes"]["r"], "50")

    def test_modify_elements_normalization(self):
        from unittest.mock import patch

        from tintamcp.server import ElementModification, modify_elements

        with patch("tintamcp.server.dispatch_operation") as mock_dispatch:
            mock_dispatch.return_value = "Updated 2 element(s): e1, e2"

            # Test passing a list
            res = modify_elements(
                elements=[
                    ElementModification(id="e1", attributes={"stroke_width": 3, "fill": "red"}),
                    ElementModification(id="e2", attributes={"r": 10}),
                ]
            )
            self.assertIn("Updated 2 element(s)", res)
            self.assertTrue(mock_dispatch.called)
            payload = mock_dispatch.call_args[0][0]
            self.assertIn("operation", payload)
            self.assertEqual(
                payload["attributes"]["elements"][0]["attributes"]["stroke-width"], "3"
            )

    def test_modify_elements_reject_empty_or_malformed_input(self):
        from typing import Any, cast
        from unittest.mock import patch

        from fastmcp.exceptions import ToolError

        from tintamcp.server import modify_elements

        with patch("tintamcp.server.dispatch_operation") as mock_dispatch:
            for items in ([], [{"attributes": {"fill": "red"}}], [{"id": "e1"}]):
                with self.subTest(items=items), self.assertRaises(ToolError):
                    cast(Any, modify_elements)(elements=items)
            mock_dispatch.assert_not_called()

    def test_delete_elements_batch_and_single_id_list(self):
        from unittest.mock import patch

        from tintamcp.server import delete_elements

        with patch("tintamcp.server.dispatch_operation") as mock_dispatch:
            mock_dispatch.return_value = "Deleted 2 element(s): e1, e2"
            res = delete_elements(ids=["e1", "e2"])
            self.assertIn("Deleted 2 element(s)", res)
            self.assertTrue(mock_dispatch.called)
            payload = mock_dispatch.call_args[0][0]
            self.assertIn("operation", payload)
            self.assertIn("e1", payload["attributes"]["ids"])
            self.assertIn("e2", payload["attributes"]["ids"])

            res_single = delete_elements(ids=["e1"])
            self.assertIn("Deleted 2 element(s)", res_single)

    def test_delete_elements_rejects_empty_list(self):
        from unittest.mock import patch

        from fastmcp.exceptions import ToolError

        from tintamcp.server import delete_elements

        with patch("tintamcp.server.dispatch_operation") as mock_dispatch:
            with self.assertRaises(ToolError):
                delete_elements(ids=[])
            mock_dispatch.assert_not_called()

    def test_apply_mask(self):
        from unittest.mock import patch

        from tintamcp.server import apply_mask

        with patch("tintamcp.server.dispatch_operation") as mock_dispatch:
            mock_dispatch.return_value = "Applied 'clip' mask using 'm1' onto 't1'"
            res = apply_mask(target_id="t1", mask_id="m1", mode="clip")
            self.assertIn("Applied 'clip' mask", res)
            payload = mock_dispatch.call_args[0][0]
            self.assertIn("operation", payload)
            self.assertEqual(payload["attributes"]["mode"], "clip")

            # Test remove
            res_rem = apply_mask(target_id="t1", remove=True)
            self.assertIn("Applied 'clip' mask", res_rem)

    def test_path_boolean_operation_validation(self):
        from typing import Any, cast

        from fastmcp.exceptions import ToolError

        from tintamcp.server import path_boolean_operation

        with self.assertRaisesRegex(ToolError, "Invalid operation"):
            cast(Any, path_boolean_operation)(operation="invalid_op", element_ids=["e1", "e2"])

        with self.assertRaisesRegex(ToolError, "requires at least 2"):
            path_boolean_operation(operation="union", element_ids=["e1"])

    def test_path_boolean_single_path_operations_keep_their_contract(self):
        from typing import Any, cast
        from unittest.mock import patch

        from tintamcp.server import path_boolean_operation

        with patch("tintamcp.server.get_inkscape_connection") as get_connection:
            connection = get_connection.return_value
            connection.is_available.return_value = True
            connection.execute_operation.return_value = {
                "status": "success",
                "data": {"tag": "path"},
            }
            connection.activate_action.return_value = True
            for operation, action in (
                ("combine", "path-combine"),
                ("break-apart", "path-break-apart"),
            ):
                with self.subTest(operation=operation):
                    cast(Any, path_boolean_operation)(operation=operation, element_ids=["path-1"])
                    self.assertTrue(
                        any(
                            call.args[0] == action
                            for call in connection.activate_action.call_args_list
                        )
                    )

    def test_mask_alias_is_rejected(self):
        from typing import Any, cast

        from fastmcp.exceptions import ToolError

        from tintamcp.server import apply_mask

        with self.assertRaisesRegex(ToolError, "Supported: 'clip', 'luminance'"):
            cast(Any, apply_mask)(target_id="target", mask_id="stencil", mode="mask")


if __name__ == "__main__":
    unittest.main()
