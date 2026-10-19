#!/usr/bin/env python3
"""
Inkscape Extension for dynamic SVG element creation
Handles any SVG element through dynamic class instantiation
"""

# Modified 2026-07 from upstream (Shriinivas/inkmcp) for native Windows support:
# fixed a hardcoded /tmp fallback path to use tempfile.gettempdir() instead.
# See README.md "Changes from upstream". AGPL-3.0, same as upstream.

import copy
import json
import os
import re
import sys
import tempfile
from pathlib import Path
from typing import Any

import inkex

# Ensure extension directory is in sys.path for direct ops imports
_ext_dir = str(Path(__file__).resolve().parent)
if _ext_dir not in sys.path:
    sys.path.insert(0, _ext_dir)

from tinta_mcp_ops.common import get_element_info_data, get_tinta_ipc_dir
from tinta_mcp_ops.document_operations import DOCUMENT_OPERATIONS
from tinta_mcp_ops.element_mapping import (
    ensure_defs_section,
    get_element_class,
    get_unique_id,
    should_place_in_defs,
)

_VALID_XML_NAME_RE = re.compile(r"^[a-zA-Z_][a-zA-Z0-9_\.\-:]*$")
_REQUEST_ID_RE = re.compile(r"^[0-9a-f]{32}$")
_LOCAL_REF_RE = re.compile(r"url\(\s*['\"]?#([^)'\"\s]+)")
_PROTOCOL_VERSION = 1
_MUTATING_OPERATIONS = set(DOCUMENT_OPERATIONS) | {"create-element", "create-elements"}


def is_valid_xml_name(name: str) -> bool:
    """Validate that an element tag or ID contains only valid XML name characters."""
    return isinstance(name, str) and bool(_VALID_XML_NAME_RE.fullmatch(name))


class ElementCreator(inkex.EffectExtension):
    """Extension for creating any SVG element dynamically"""

    def add_arguments(self, pars):
        """Add command line arguments"""
        # No parameters needed - use fixed file path like original system
        pass

    def _claim_id(self, svg, tag: str, requested_id=None) -> str:
        used_ids = getattr(self, "_used_ids", None)
        if used_ids is None:
            used_ids = {element.get("id") for element in svg.iter() if element.get("id")}
            self._used_ids = used_ids
        reserved_ids = getattr(self, "_reserved_ids", set())
        claimed_ids = getattr(self, "_claimed_ids", set())
        if requested_id is not None:
            actual_id = str(requested_id)
            if not is_valid_xml_name(actual_id):
                raise ValueError(f"Invalid XML ID: '{actual_id}'")
            if actual_id in claimed_ids or (
                actual_id in used_ids and actual_id not in reserved_ids
            ):
                raise ValueError(f"ID '{actual_id}' already exists")
            claimed_ids.add(actual_id)
        else:
            base_id = get_unique_id(svg, tag, None)
            actual_id = base_id
            suffix = 1
            while actual_id in used_ids or actual_id in reserved_ids:
                actual_id = f"{base_id}_{suffix}"
                suffix += 1
        used_ids.add(actual_id)
        return actual_id

    def _prepare_ids(self, svg, elements: list[dict[str, Any]], group_id=None) -> None:
        used_ids = {element.get("id") for element in svg.iter() if element.get("id")}
        reserved_ids = set()
        claimed_ids = set()

        def visit(spec):
            if not isinstance(spec, dict) or not isinstance(spec.get("attributes", {}), dict):
                raise ValueError("Each element must have an attributes object")
            tag = spec.get("tag", "")
            if not isinstance(tag, str) or not is_valid_xml_name(tag):
                raise ValueError(f"Invalid XML tag name: '{tag}'")
            requested_id = spec.get("attributes", {}).get("id")
            if requested_id is not None:
                if not isinstance(requested_id, str) or not is_valid_xml_name(requested_id):
                    raise ValueError(f"Invalid XML ID: '{requested_id}'")
                if requested_id in used_ids or requested_id in reserved_ids:
                    raise ValueError(f"ID '{requested_id}' already exists")
                reserved_ids.add(requested_id)
            children = spec.get("children", [])
            if not isinstance(children, list):
                raise ValueError("children must be a list")
            for child in children:
                visit(child)

        if group_id:
            if not isinstance(group_id, str) or not is_valid_xml_name(group_id):
                raise ValueError(f"Invalid XML ID: '{group_id}'")
            if group_id in used_ids:
                raise ValueError(f"ID '{group_id}' already exists")
            reserved_ids.add(group_id)
        for element in elements:
            visit(element)

        valid_ids = used_ids | reserved_ids
        for element in elements:
            for spec in self._walk_elements(element):
                for name, value in spec.get("attributes", {}).items():
                    if (
                        name in ("href", "xlink:href")
                        and isinstance(value, str)
                        and value.startswith("#")
                    ):
                        if value[1:] not in valid_ids:
                            raise ValueError(f"Referenced ID '{value[1:]}' does not exist")
                    if isinstance(value, str):
                        for reference in _LOCAL_REF_RE.findall(value):
                            if reference not in valid_ids:
                                raise ValueError(f"Referenced ID '{reference}' does not exist")

        self._used_ids = used_ids
        self._reserved_ids = reserved_ids
        self._claimed_ids = claimed_ids

    @staticmethod
    def _walk_elements(spec):
        yield spec
        for child in spec.get("children", []):
            yield from ElementCreator._walk_elements(child)

    # def errormsg(self, msg):
    #     """Override errormsg to prevent UI dialogs - silent operation only"""
    #     # Don't call parent errormsg to avoid UI dialogs
    #     pass

    # def debug(self, msg):
    #     """Override debug to suppress debug messages"""
    #     # Suppress all debug output to avoid UI interference
    #     pass

    def create_element_recursive(
        self,
        svg,
        element_data: dict[str, Any],
        id_mapping: dict[str, str] | None = None,
        generated_ids: list[str] | None = None,
    ) -> inkex.BaseElement:
        """
        Create SVG element recursively with children and track ID mappings

        Args:
            svg: SVG document
            element_data: Element data with tag, attributes, and children
            id_mapping: Dictionary to collect requested_id -> actual_id mappings
            generated_ids: List to collect auto-generated IDs

        Returns:
            Created SVG element
        """
        if id_mapping is None:
            id_mapping = {}
        if generated_ids is None:
            generated_ids = []

        tag = element_data.get("tag", "")
        if tag and not is_valid_xml_name(tag):
            raise ValueError(f"Invalid XML tag name: '{tag}'")

        attributes = element_data.get("attributes", {})
        children = element_data.get("children", [])

        # Get element class dynamically
        ElementClass = get_element_class(tag)

        if ElementClass:
            # Create element using inkex class
            element = ElementClass()
        else:
            # Fallback to raw lxml element for unmapped tags (like filter primitives)
            element = inkex.etree.Element(tag)

        # Handle ID parameter - track both requested and generated
        requested_id = attributes.get("id")
        if requested_id is not None:
            actual_id = self._claim_id(svg, tag, requested_id)
            # Track mapping for response
            id_mapping[requested_id] = actual_id
        else:
            # No ID specified - auto-generate and track
            actual_id = self._claim_id(svg, tag)
            generated_ids.append(actual_id)

        element.set("id", actual_id)

        # Set all attributes except id (already handled)
        for attr_name, attr_value in attributes.items():
            if attr_name != "id":
                if attr_name == "text":
                    element.text = str(attr_value)
                else:
                    element.set(attr_name, str(attr_value))

        # Process children recursively with same tracking lists
        for child_data in children:
            child_element = self.create_element_recursive(
                svg, child_data, id_mapping, generated_ids
            )
            element.append(child_element)

        return element

    def handle_operation(self, svg, action: str, attributes: dict[str, Any]) -> dict[str, Any]:
        """
        Dispatch a known structured document operation or query

        Args:
            svg: SVG document
            action: Action name (e.g., 'get-selection', 'get-info')
            attributes: Action parameters

        Returns:
            Response data
        """
        try:
            if action in DOCUMENT_OPERATIONS:
                return DOCUMENT_OPERATIONS[action](svg, attributes)
            elif action == "get-selection":
                return self.get_selection_info()
            elif action == "get-info":
                return self.get_document_info(svg)
            elif action == "get-info-by-id":
                element_id = attributes.get("id", "")
                return self.get_element_info(svg, element_id)
            elif action == "list-elements":
                limit = attributes.get("limit", 100)
                offset = attributes.get("offset", 0)
                if type(limit) is not int or not 1 <= limit <= 500:
                    raise ValueError("limit must be an integer from 1 to 500")
                if type(offset) is not int or offset < 0:
                    raise ValueError("offset must be a non-negative integer")
                summaries = []
                total = 0
                for node in svg.iter():
                    if node is svg or not isinstance(node.tag, str):
                        continue
                    if offset <= total < offset + limit:
                        parent = node.getparent()
                        depth = 0
                        ancestor = parent
                        while ancestor is not None and ancestor is not svg:
                            depth += 1
                            ancestor = ancestor.getparent()
                        summaries.append(
                            {
                                "id": node.get("id"),
                                "tag": node.tag.split("}")[-1],
                                "label": node.get(
                                    "{http://www.inkscape.org/namespaces/inkscape}label"
                                ),
                                "parent_id": parent.get("id") if parent is not svg else None,
                                "depth": depth,
                            }
                        )
                    total += 1
                return {
                    "status": "success",
                    "data": {
                        "message": "Document elements",
                        "total": total,
                        "offset": offset,
                        "limit": limit,
                        "elements": summaries,
                    },
                }
            else:
                return {
                    "status": "error",
                    "data": {"error": f"Unsupported operation: {action}"},
                }
        except Exception as e:
            return {
                "status": "error",
                "data": {"error": f"Operation failed: {str(e)}"},
            }

    def get_selection_info(self) -> dict[str, Any]:
        """Get information about current selection"""
        try:
            # Get selected elements - Inkscape passes them via self.svg.selected
            selected = self.svg.selected

            # Extract info for each selected element
            elements = []
            for elem_id, element in selected.items():
                elem_info = get_element_info_data(element)
                elements.append(elem_info)

            return {
                "status": "success",
                "data": {
                    "message": "Selection information retrieved successfully",
                    "count": len(selected),
                    "elements": elements,
                },
            }
        except Exception as e:
            return {
                "status": "error",
                "data": {"error": f"Failed to get selection info: {str(e)}"},
            }

    def get_document_info(self, svg) -> dict[str, Any]:
        """Get document information"""
        try:
            viewbox_value = svg.get("viewBox")
            viewbox = (
                re.split(r"[\s,]+", viewbox_value.strip()) if viewbox_value is not None else None
            )
            width = svg.get("width", "unknown")
            height = svg.get("height", "unknown")
            width_unit = re.sub(r"^[+-]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][+-]?\d+)?", "", width)
            height_unit = re.sub(r"^[+-]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][+-]?\d+)?", "", height)

            # Count elements by type
            element_counts = {}
            for elem in svg.iter():
                if not isinstance(elem.tag, str):
                    continue
                tag = elem.tag.split("}")[-1] if "}" in elem.tag else elem.tag
                element_counts[tag] = element_counts.get(tag, 0) + 1

            return {
                "status": "success",
                "data": {
                    "message": "Document information",
                    "dimensions": {"width": width, "height": height},
                    "units": {"width": width_unit, "height": height_unit},
                    "viewBox": viewbox,
                    "elementCounts": element_counts,
                },
            }
        except Exception as e:
            return {
                "status": "error",
                "data": {"error": f"Failed to get document info: {str(e)}"},
            }

    def get_element_info(self, svg, element_id: str) -> dict[str, Any]:
        """Get information about specific element"""
        try:
            element = svg.getElementById(element_id)
            if element is None:
                return {
                    "status": "error",
                    "data": {"error": f"Element not found: {element_id}"},
                }

            element_info = get_element_info_data(element)
            return {
                "status": "success",
                "data": {
                    "message": f"Element information for {element_id}",
                    **element_info,
                },
            }
        except Exception as e:
            return {
                "status": "error",
                "data": {"error": f"Failed to get element info: {str(e)}"},
            }

    def write_response(self, response_data, request, ipc_dir):
        """Publish a correlated response after inkex has serialized the SVG."""
        response = {
            **response_data,
            "protocol_version": request["protocol_version"],
            "session_id": request["session_id"],
            "request_id": request["request_id"],
            "operation": request["operation"],
        }
        response_path = ipc_dir / f"mcp_response_{request['request_id']}.json"
        fd, temporary_path = tempfile.mkstemp(
            prefix=f".{response_path.name}.", suffix=".tmp", dir=str(ipc_dir)
        )
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as stream:
                json.dump(response, stream, ensure_ascii=False)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary_path, response_path)
        except Exception:
            try:
                os.unlink(temporary_path)
            except OSError:
                pass
            raise

    def save_raw(self, ret):
        """Save the SVG first, then publish the response for its request."""
        super().save_raw(ret)
        pending = getattr(self, "_tinta_pending_response", None)
        if pending is not None:
            response, request, ipc_dir, processing_file = pending
            processing_file.unlink(missing_ok=True)
            self.write_response(response, request, ipc_dir)
            self._tinta_pending_response = None

    def effect(self):
        """Main extension entry point"""
        element_data = {}
        correlation = None
        original_svg = None
        processing_file = None
        try:
            ipc_dir = get_tinta_ipc_dir()
            params_file = ipc_dir / "mcp_params.json"
            processing_file = ipc_dir / "mcp_processing.json"
            try:
                with processing_file.open("x", encoding="ascii"):
                    pass
            except FileExistsError:
                return
            if params_file.is_symlink() or not params_file.is_file():
                processing_file.unlink(missing_ok=True)
                return

            with params_file.open(encoding="utf-8") as f:
                element_data = json.load(f)
            params_file.unlink(missing_ok=True)

            request_id = element_data.get("request_id")
            session_id = element_data.get("session_id")
            if (
                not isinstance(request_id, str)
                or not _REQUEST_ID_RE.fullmatch(request_id)
                or not isinstance(session_id, str)
                or not _REQUEST_ID_RE.fullmatch(session_id)
            ):
                return
            request_payload = element_data.get("request")
            correlation = {
                "protocol_version": element_data.get("protocol_version"),
                "session_id": session_id,
                "request_id": request_id,
                "operation": (
                    request_payload.get("operation") if isinstance(request_payload, dict) else None
                ),
            }
            if element_data.get("protocol_version") != _PROTOCOL_VERSION:
                response = {
                    "status": "error",
                    "data": {"error": "Unsupported IPC protocol version"},
                }
                self._tinta_pending_response = (response, correlation, ipc_dir, processing_file)
                return

            if not isinstance(request_payload, dict):
                raise ValueError("Versioned IPC request must contain a request object")
            element_data = {**request_payload, **correlation}
            operation = element_data.get("operation", "")
            if not isinstance(operation, str) or not isinstance(
                element_data.get("attributes", {}), dict
            ):
                raise ValueError("Invalid operation request format")
            if operation in _MUTATING_OPERATIONS:
                original_svg = self.svg
                self.svg = copy.deepcopy(original_svg)
                self.document = self.svg.getroottree()
                self._used_ids = {
                    element.get("id") for element in self.svg.iter() if element.get("id")
                }

            # Handle batch creation of multiple SVG elements
            if operation == "create-elements":
                # 1. Clear canvas if requested
                cleared_count = 0
                if element_data.get("clear_canvas"):
                    clear_result = DOCUMENT_OPERATIONS["clear-canvas"](self.svg, {})
                    if clear_result.get("status") != "success":
                        raise ValueError(
                            clear_result.get("data", {}).get("error", "Canvas clear failed")
                        )
                    cleared_count = clear_result.get("data", {}).get("count", 0)
                    self._used_ids = {
                        element.get("id") for element in self.svg.iter() if element.get("id")
                    }

                # 2. Set canvas size if requested
                canvas_size = element_data.get("canvas_size")
                if isinstance(canvas_size, dict):
                    cw = float(canvas_size.get("width", 800))
                    ch = float(canvas_size.get("height", 800))
                    cu = canvas_size.get("unit", "px")
                    DOCUMENT_OPERATIONS["set-canvas"](
                        self.svg, {"width": cw, "height": ch, "unit": cu}
                    )

                elements_list = element_data.get("elements", [])
                if not isinstance(elements_list, list) or any(
                    not isinstance(item, dict) for item in elements_list
                ):
                    raise ValueError("elements must be a list of element objects")
                group_id = element_data.get("group_id")
                if group_id is not None and not isinstance(group_id, str):
                    raise ValueError("group_id must be a string")
                self._prepare_ids(self.svg, elements_list, group_id)
                id_mapping = {}
                generated_ids = []
                created_ids = []

                target_container = None
                actual_group_id = None
                if group_id:
                    GroupClass = get_element_class("g")
                    group_element = GroupClass() if GroupClass else inkex.etree.Element("g")
                    actual_group_id = self._claim_id(self.svg, "g", group_id)
                    group_element.set("id", actual_group_id)
                    id_mapping[group_id] = actual_group_id
                    target_container = group_element
                else:
                    current_layer = self.svg.get_current_layer()
                    target_container = current_layer if current_layer is not None else self.svg

                for item in elements_list:
                    elem = self.create_element_recursive(self.svg, item, id_mapping, generated_ids)
                    ElemClass = get_element_class(item.get("tag", ""))
                    if ElemClass and should_place_in_defs(ElemClass):
                        defs = ensure_defs_section(self.svg)
                        defs.append(elem)
                    else:
                        target_container.append(elem)
                    created_ids.append(elem.get("id"))

                if group_id and target_container is not None:
                    current_layer = self.svg.get_current_layer()
                    if current_layer is not None:
                        current_layer.append(target_container)
                    else:
                        self.svg.append(target_container)

                # 3. Fit canvas around elements if requested
                if element_data.get("fit_canvas"):
                    DOCUMENT_OPERATIONS["fit-canvas"](
                        self.svg, {"padding": element_data.get("fit_padding", 20.0)}
                    )

                # 4. Select created elements in UI if requested
                if element_data.get("select_created") and created_ids:
                    try:
                        matched = [el for el in self.svg.iter() if el.get("id") in created_ids]
                        if hasattr(self.svg, "selection") and matched:
                            self.svg.selection.set(*matched)
                    except Exception:
                        pass

                suffix = f" in group '{actual_group_id}'" if actual_group_id else ""
                response_data = {
                    "message": f"{len(created_ids)} elements created successfully{suffix}",
                    "count": len(created_ids),
                    "created_ids": created_ids,
                    "id_mapping": id_mapping,
                    "generated_ids": generated_ids,
                }
                if actual_group_id:
                    response_data["group_id"] = actual_group_id
                if element_data.get("clear_canvas"):
                    response_data["cleared_elements"] = cleared_count
                if element_data.get("fit_canvas"):
                    response_data["fitted_canvas"] = True

                response = {
                    "status": "success",
                    "data": response_data,
                }

            # Try to create as SVG element first
            elif operation == "create-element":
                spec = element_data.get("element", {})
                tag = spec.get("tag", "")
                self._prepare_ids(self.svg, [spec])
                ElementClass = get_element_class(tag)
                if ElementClass is None:
                    raise ValueError(f"Unsupported element tag: {tag}")
                # Create SVG element with ID tracking
                id_mapping = {}
                generated_ids = []
                element = self.create_element_recursive(self.svg, spec, id_mapping, generated_ids)

                # Determine placement
                if should_place_in_defs(ElementClass):
                    defs = ensure_defs_section(self.svg)
                    defs.append(element)
                else:
                    # Place in active layer if available, otherwise in svg root
                    current_layer = self.svg.get_current_layer()
                    if current_layer is not None:
                        current_layer.append(element)
                    else:
                        self.svg.append(element)

                # Build response data
                response_data = {
                    "message": f"{tag} created successfully",
                    "id": element.get("id"),
                    "tag": tag,
                    "attributes": dict(element.attrib),
                }

                # Add ID information to response
                total_elements = len(id_mapping) + len(generated_ids)

                if id_mapping:
                    response_data["id_mapping"] = id_mapping

                if generated_ids:
                    response_data["generated_ids"] = generated_ids

                # Update message to reflect multiple elements if needed
                if total_elements > 1:
                    response_data["message"] = f"{total_elements} elements created successfully"

                response = {
                    "status": "success",
                    "data": response_data,
                }

            else:
                # Dispatch a fixed operation or query
                attributes = element_data.get("attributes", {})
                response = self.handle_operation(self.svg, operation, attributes)

            if original_svg is not None and response.get("status") != "success":
                self.svg = original_svg
                self.document = self.svg.getroottree()
            self._tinta_pending_response = (response, correlation, ipc_dir, processing_file)

        except Exception as e:
            if original_svg is not None:
                self.svg = original_svg
                self.document = self.svg.getroottree()
            error_response = {
                "status": "error",
                "data": {"error": f"Extension failed: {str(e)}"},
            }
            if correlation is not None and processing_file is not None:
                self._tinta_pending_response = (
                    error_response,
                    correlation,
                    ipc_dir,
                    processing_file,
                )


def main():
    """Main entry point"""
    extension = ElementCreator()
    extension.run()


if __name__ == "__main__":
    main()
