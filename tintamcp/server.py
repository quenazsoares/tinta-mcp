#!/usr/bin/env python3
"""
Inkscape MCP Server
Model Context Protocol server for controlling Inkscape via D-Bus extension

Provides access to Inkscape operations through a unified tool interface
for structured SVG element creation and document manipulation.
"""

# Modified 2026-07 from upstream (Shriinivas/inkmcp) for native Windows support:
# gdbus resolution via gdbus_util instead of a bare "gdbus", and response-file polling.
# See README.md "Changes from upstream". AGPL-3.0, same as upstream.

import argparse
import json
import logging
import os
import re
import stat
import struct
import subprocess
import tempfile
import threading
import time
import uuid
import zlib
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from typing import Annotated, Any, Literal

from fastmcp import FastMCP
from fastmcp.exceptions import ToolError
from mcp.types import ImageContent, TextContent
from pydantic import BaseModel, ConfigDict, Field

from tintamcp.gdbus_util import (
    find_gdbus,
    get_tinta_ipc_dir,
    ipc_process_lock,
    response_path,
    wait_for_response_file,
)

# Configure logging
logging.basicConfig(
    level=logging.INFO, format="%(asctime)s - %(name)s - %(levelname)s - %(message)s"
)
logger = logging.getLogger("TintaMCP")

# Server configuration
DEFAULT_DBUS_SERVICE = "org.inkscape.Inkscape"
DEFAULT_DBUS_PATH = "/org/inkscape/Inkscape"
DEFAULT_DBUS_INTERFACE = "org.gtk.Actions"
DEFAULT_ACTION_NAME = "org.tinta.mcp"


def _is_complete_png(data: bytes) -> bool:
    if len(data) < 45 or data[:8] != b"\x89PNG\r\n\x1a\n":
        return False
    offset = 8
    seen_header = seen_data = seen_end = False
    while offset + 12 <= len(data):
        length = struct.unpack_from(">I", data, offset)[0]
        chunk_end = offset + 12 + length
        if chunk_end > len(data):
            return False
        chunk_type = data[offset + 4 : offset + 8]
        chunk_data = data[offset + 8 : offset + 8 + length]
        expected_crc = struct.unpack_from(">I", data, offset + 8 + length)[0]
        if zlib.crc32(chunk_type + chunk_data) & 0xFFFFFFFF != expected_crc:
            return False
        if not seen_header:
            if chunk_type != b"IHDR" or length != 13:
                return False
            seen_header = True
        elif chunk_type == b"IDAT":
            seen_data = True
        elif chunk_type == b"IEND":
            if length != 0 or not seen_data or chunk_end != len(data):
                return False
            seen_end = True
            break
        offset = chunk_end
    return seen_header and seen_data and seen_end


def _write_json_atomic(path, data: dict[str, Any]) -> None:
    """Publish a JSON file by replacing its destination after the write completes."""
    fd, temporary_path = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            json.dump(data, stream, ensure_ascii=False)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary_path, path)
    except Exception:
        try:
            os.unlink(temporary_path)
        except OSError:
            pass
        raise


def _read_json_file(path) -> dict[str, Any]:
    info = path.lstat()
    if not stat.S_ISREG(info.st_mode):
        raise ValueError(f"IPC path is not a regular file: {path.name}")
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"IPC JSON must contain an object: {path.name}")
    return value


def _finish_ipc_request(ipc_dir, request, response_file) -> bool:
    """Remove only this request's files, leaving reconciliation evidence on failure."""
    params_file = ipc_dir / "mcp_params.json"
    if params_file.exists():
        try:
            params = _read_json_file(params_file)
            if params.get("request_id") != request.get("request_id"):
                return False
            params_file.unlink()
        except (OSError, ValueError, AttributeError):
            return False

    pending_file = ipc_dir / "pending_request.json"
    try:
        pending_file.unlink(missing_ok=True)
    except OSError:
        return False

    try:
        response_file.unlink(missing_ok=True)
    except OSError:
        logger.warning("Could not remove completed IPC response %s", response_file)
    return True


class InkscapeConnection:
    """Manages D-Bus connection to Inkscape with TTL caching and user-isolated IPC"""

    def __init__(self):
        self.dbus_service = DEFAULT_DBUS_SERVICE
        self.dbus_path = DEFAULT_DBUS_PATH
        self.dbus_interface = DEFAULT_DBUS_INTERFACE
        self.action_name = DEFAULT_ACTION_NAME
        self._lock = threading.Lock()
        self._last_checked: float = 0.0
        self._is_available_cached: bool = False
        self._cache_ttl: float = 5.0  # Cache availability for 5 seconds
        self._session_id = uuid.uuid4().hex

    def is_available(self, force: bool = False) -> bool:
        """Check if Inkscape is running and MCP extension is available, with TTL caching."""
        now = time.monotonic()
        if not force and (now - self._last_checked < self._cache_ttl):
            return self._is_available_cached

        try:
            cmd = [
                find_gdbus(),
                "call",
                "--session",
                "--dest",
                self.dbus_service,
                "--object-path",
                self.dbus_path,
                "--method",
                f"{self.dbus_interface}.List",
            ]
            result = subprocess.run(cmd, capture_output=True, text=True, timeout=5)

            if result.returncode != 0:
                logger.warning("Inkscape D-Bus service not available")
                self._is_available_cached = False
            else:
                self._is_available_cached = self.action_name in result.stdout

        except Exception as e:
            logger.error(f"Error checking Inkscape availability: {e}")
            self._is_available_cached = False

        self._last_checked = now
        return self._is_available_cached

    def activate_action(self, action_name: str, param_str: str = "[]") -> bool:
        """Activate a native Inkscape GTK action via D-Bus."""
        try:
            cmd = [
                find_gdbus(),
                "call",
                "--session",
                "--dest",
                self.dbus_service,
                "--object-path",
                self.dbus_path,
                "--method",
                f"{self.dbus_interface}.Activate",
                action_name,
                param_str,
                "{}",
            ]
            res = subprocess.run(cmd, capture_output=True, text=True, timeout=10)
            return res.returncode == 0
        except Exception as e:
            logger.error(f"Error activating native action {action_name}: {e}")
            return False

    def export_native_preview(self, max_size: int = 800, area: str = "page") -> ImageContent | None:
        """Export a bounded PNG through native Inkscape actions only."""
        import base64
        import uuid

        if type(max_size) is not int or not 1 <= max_size <= 4096 or area != "page":
            return None

        info = self.execute_operation({"operation": "get-info", "attributes": {}})
        dimensions = info.get("data", {}).get("dimensions", {})
        factors = {
            "": 1.0,
            "px": 1.0,
            "mm": 96 / 25.4,
            "cm": 96 / 2.54,
            "in": 96.0,
            "pt": 96 / 72,
            "pc": 16.0,
        }

        def pixels(value):
            match = re.fullmatch(
                r"\s*([+-]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][+-]?\d+)?)\s*([a-zA-Z]*)\s*",
                str(value),
            )
            if match is None or match.group(2) not in factors:
                return None
            size = float(match.group(1)) * factors[match.group(2)]
            return size if size > 0 and size < float("inf") else None

        width, height = pixels(dimensions.get("width")), pixels(dimensions.get("height"))
        if width is None or height is None:
            return None
        size_action = "export-width" if width >= height else "export-height"

        with self._lock:
            ipc_dir = get_tinta_ipc_dir()
            out_file = ipc_dir / f"native_preview_{uuid.uuid4().hex}.png"
            out_file_str = str(out_file).replace("\\", "/")
            try:
                actions = (
                    ("export-type", '[<"png">]'),
                    ("export-filename", f"[<{json.dumps(out_file_str, ensure_ascii=False)}>]"),
                    ("export-overwrite", "[<false>]"),
                    ("export-area-page", "[<true>]"),
                    ("export-dpi", "[<96.0>]"),
                    (size_action, f"[<{max_size}>]"),
                )
                for action, parameter in actions:
                    if not self.activate_action(action, parameter):
                        logger.warning("Could not prepare native preview action %s", action)
                        return None
                if not self.activate_action("export-do", "[]"):
                    return None

                deadline = time.monotonic() + 3.0
                while time.monotonic() < deadline:
                    if out_file.exists() and out_file.stat().st_size >= 24:
                        break
                    time.sleep(0.04)

                if out_file.exists() and 24 <= out_file.stat().st_size <= 50_000_000:
                    data = out_file.read_bytes()
                    if not _is_complete_png(data):
                        return None
                    pixel_width, pixel_height = struct.unpack(">II", data[16:24])
                    if max(pixel_width, pixel_height) > max_size:
                        return None
                    data = base64.b64encode(data).decode("ascii")
                    return ImageContent(type="image", data=data, mime_type="image/png")
            except Exception as e:
                logger.error(f"Native export failed: {e}")
            finally:
                if out_file.exists():
                    try:
                        out_file.unlink()
                    except OSError:
                        pass
            return None

    def execute_operation(self, operation_data: dict[str, Any]) -> dict[str, Any]:
        """Send one correlated request through the Inkscape extension."""
        with self._lock:
            ipc_dir = get_tinta_ipc_dir()
            params_file = ipc_dir / "mcp_params.json"
            try:
                with ipc_process_lock(ipc_dir):
                    pending_file = ipc_dir / "pending_request.json"
                    if pending_file.exists():
                        try:
                            pending = _read_json_file(pending_file)
                            old_response = response_path(ipc_dir, pending["request_id"])
                            if not wait_for_response_file(str(old_response), timeout=0):
                                return {
                                    "status": "error",
                                    "data": {
                                        "error": "Previous request is still pending; its outcome is unknown",
                                        "request_id": pending["request_id"],
                                    },
                                }
                            old_result = _read_json_file(old_response)
                            self._validate_response(old_result, pending)
                            if not _finish_ipc_request(ipc_dir, pending, old_response):
                                return {
                                    "status": "error",
                                    "data": {
                                        "error": "Previous request files could not be reconciled"
                                    },
                                }
                        except Exception as error:
                            return {
                                "status": "error",
                                "data": {
                                    "error": f"Previous request cannot be reconciled: {error}"
                                },
                            }

                    if params_file.exists():
                        return {
                            "status": "error",
                            "data": {
                                "error": "A stale request file exists; refusing to overwrite it"
                            },
                        }
                    if (ipc_dir / "mcp_processing.json").exists():
                        return {
                            "status": "error",
                            "data": {"error": "A previous extension request is still processing"},
                        }

                    reserved = {"protocol_version", "session_id", "request_id", "response_file"}
                    if reserved.intersection(operation_data):
                        return {
                            "status": "error",
                            "data": {
                                "error": "Reserved IPC fields cannot be supplied by an operation"
                            },
                        }

                    operation = operation_data.get("operation")
                    if not isinstance(operation, str) or not operation:
                        return {
                            "status": "error",
                            "data": {"error": "Operation name is required"},
                        }
                    request = {
                        "protocol_version": 1,
                        "session_id": self._session_id,
                        "request_id": uuid.uuid4().hex,
                        "request": operation_data,
                    }
                    response_file = response_path(ipc_dir, request["request_id"])
                    command = [
                        find_gdbus(),
                        "call",
                        "--session",
                        "--dest",
                        self.dbus_service,
                        "--object-path",
                        self.dbus_path,
                        "--method",
                        f"{self.dbus_interface}.Activate",
                        self.action_name,
                        "[]",
                        "{}",
                    ]
                    try:
                        _write_json_atomic(
                            pending_file,
                            {
                                "protocol_version": request["protocol_version"],
                                "session_id": request["session_id"],
                                "request_id": request["request_id"],
                                "operation": operation,
                            },
                        )
                        _write_json_atomic(params_file, request)
                    except Exception:
                        params_file.unlink(missing_ok=True)
                        pending_file.unlink(missing_ok=True)
                        raise
                    try:
                        result = subprocess.run(command, capture_output=True, text=True, timeout=30)
                    except FileNotFoundError as error:
                        params_file.unlink(missing_ok=True)
                        pending_file.unlink(missing_ok=True)
                        return {"status": "error", "data": {"error": str(error)}}
                    except (subprocess.TimeoutExpired, OSError) as error:
                        return self._unknown_result(request, f"D-Bus activation failed: {error}")

                    if result.returncode != 0:
                        self._is_available_cached = False
                        self._last_checked = 0.0
                        return self._unknown_result(request, f"D-Bus call failed: {result.stderr}")

                    if not wait_for_response_file(str(response_file)):
                        return self._unknown_result(
                            request, "No extension response; outcome is unknown"
                        )
                    try:
                        response_data = _read_json_file(response_file)
                        self._validate_response(response_data, request)
                    except Exception as error:
                        return self._unknown_result(request, f"Invalid extension response: {error}")

                    if not _finish_ipc_request(ipc_dir, request, response_file):
                        logger.warning(
                            "IPC request %s completed, but cleanup will be retried",
                            request["request_id"],
                        )
                    return response_data
            except Exception as error:
                logger.error(f"Operation execution error: {error}")
                return {"status": "error", "data": {"error": str(error)}}

    @staticmethod
    def _validate_response(response: dict[str, Any], request: dict[str, Any]) -> None:
        for key in ("protocol_version", "session_id", "request_id"):
            if response.get(key) != request.get(key):
                raise ValueError(f"IPC response correlation mismatch for {key}")
        operation = request.get("operation") or request.get("request", {}).get("operation")
        if response.get("operation") != operation:
            raise ValueError("IPC response correlation mismatch for operation")
        if response.get("protocol_version") != 1:
            raise ValueError("Unsupported IPC response version")
        if response.get("status") not in ("success", "error") or not isinstance(
            response.get("data"), dict
        ):
            raise ValueError("Invalid IPC response format")

    @staticmethod
    def _unknown_result(request: dict[str, Any], message: str) -> dict[str, Any]:
        logger.error("IPC request %s has unknown outcome: %s", request["request_id"], message)
        return {
            "status": "error",
            "protocol_version": 1,
            "session_id": request["session_id"],
            "request_id": request["request_id"],
            "operation": request.get("operation") or request.get("request", {}).get("operation"),
            "data": {
                "error": f"Operation outcome unknown for request {request['request_id']}: {message}"
            },
        }


# Global connection instance
_inkscape_connection: InkscapeConnection | None = None


def get_inkscape_connection() -> InkscapeConnection:
    """Get or create Inkscape connection"""
    global _inkscape_connection

    if _inkscape_connection is None:
        _inkscape_connection = InkscapeConnection()

    if not _inkscape_connection.is_available():
        raise Exception(
            "Inkscape is not running or the Inkscape MCP extension is not available. "
            "Please start Inkscape and ensure the extension is installed."
        )

    return _inkscape_connection


@asynccontextmanager
async def server_lifespan(server: FastMCP) -> AsyncGenerator[dict[str, Any], None]:
    """Manage server startup and shutdown lifecycle"""
    logger.info("Inkscape MCP server starting up")

    try:
        # Test connection on startup
        try:
            get_inkscape_connection()
            logger.info("Successfully connected to Inkscape on startup")
        except Exception as e:
            logger.warning(f"Could not connect to Inkscape on startup: {e}")
            logger.warning(
                "Make sure Inkscape is running with the Inkscape MCP extension before using tools"
            )

        yield {}
    finally:
        logger.info("Inkscape MCP server shut down")


# Create the MCP server
mcp = FastMCP("TintaMCP", lifespan=server_lifespan)


def format_response(result: dict[str, Any]) -> str:
    """Format operation result for clean AI client display"""
    if result.get("status") == "success":
        data = result.get("data", {})
        message = data.get("message", "Operation completed successfully")

        if any(
            key in data
            for key in (
                "dimensions",
                "viewBox",
                "units",
                "elementCounts",
                "elements",
                "attributes",
                "style",
                "offset",
                "limit",
                "total",
            )
        ):
            return f"{message}\n{json.dumps(data, ensure_ascii=False, sort_keys=True)}"

        # Add relevant details based on operation type
        details = []

        # Element creation details
        if "id" in data:
            details.append(f"**ID**: `{data['id']}`")
        if "tag" in data:
            details.append(f"**Type**: {data['tag']}")

        # Selection/info details
        if "count" in data:
            details.append(f"**Count**: {data['count']}")
        if "elements" in data:
            elements = data["elements"]
            if elements:
                details.append(f"**Elements**: {len(elements)} items")
                # Show first few elements
                for i, elem in enumerate(elements[:3]):
                    elem_desc = f"{elem.get('tag', 'unknown')} ({elem.get('id', 'no-id')})"
                    details.append(f"  {i + 1}. {elem_desc}")
                if len(elements) > 3:
                    details.append(f"  ... and {len(elements) - 3} more")

        # Export details
        if "export_path" in data:
            details.append(f"**File**: {data['export_path']}")
        if "file_size" in data:
            details.append(f"**Size**: {data['file_size']} bytes")

        # Batch creation details
        if "count" in data:
            details.append(f"**Batch Total**: {data['count']} elements")
        if "group_id" in data:
            details.append(f"**Parent Group**: `{data['group_id']}`")

        # ID mapping (requested → actual)
        if data.get("id_mapping"):
            details.append("**Element IDs** (requested → actual):")
            for requested_id, actual_id in data["id_mapping"].items():
                if requested_id == actual_id:
                    details.append(f"  {requested_id} ✓")
                else:
                    details.append(f"  {requested_id} → {actual_id} (collision resolved)")

        # Warning for missing IDs
        if data.get("generated_ids"):
            details.append("⚠️  **WARNING: Elements created without IDs**")
            details.append("For better scene management, always specify 'id' for elements:")
            for gen_id in data["generated_ids"]:
                # Extract element type from generated ID (e.g., "circle2863" → "circle")
                elem_type = "".join(c for c in gen_id if c.isalpha())
                details.append(f"  {gen_id} (set id in the {elem_type} element specification)")
            details.append("This enables later modification with tools like modify_elements.")

        emoji = "✅"

        if details:
            return f"{emoji} {message}\n\n" + "\n".join(details)
        else:
            return f"{emoji} {message}"

    else:
        error = result.get("data", {}).get("error", "Unknown error")
        return f"❌ {error}"


def dispatch_operation(payload: dict[str, Any]) -> str:
    """Helper to dispatch operation via D-Bus and collect response cleanly"""
    try:
        connection = get_inkscape_connection()
        logger.info(f"Executing operation: {payload.get('operation')}")
        result = connection.execute_operation(payload)

        if result.get("status") != "success":
            error = result.get("data", {}).get("error", "Inkscape operation failed")
            raise ToolError(str(error))

        return format_response(result)
    except ToolError:
        raise
    except Exception as e:
        logger.error(f"Error in dispatch_operation: {e}")
        raise ToolError(f"Operation failed: {e!s}") from e


def _attach_preview(result: str, max_size: int) -> list[TextContent | ImageContent] | str:
    """Keep the applied operation result when a requested preview is unavailable."""
    _validate_preview_size(max_size)
    try:
        preview = get_inkscape_connection().export_native_preview(max_size=max_size)
    except Exception as error:
        logger.warning("Native preview failed after the operation was applied: %s", error)
        preview = None
    if preview is None:
        return f"{result}\nWarning: the operation was applied, but its preview is unavailable."
    return [TextContent(type="text", text=result), preview]


def _validate_preview_size(max_size: int) -> None:
    if type(max_size) is not int or not 1 <= max_size <= 4096:
        raise ToolError("preview_max_size must be an integer from 1 to 4096 pixels")


@mcp.tool()
def create_shape(
    element_type: Literal["rect", "circle", "ellipse", "line", "polygon", "polyline"],
    id: str,
    attributes: dict[str, Any] | None = None,
) -> str | ImageContent:
    """Create a standard SVG shape element (rect, circle, ellipse, line, polygon, or polyline).

    Args:
        element_type: SVG shape tag. Use create_group to create a group.
        id: Unique identifier for the element.
        attributes: Dictionary of SVG attributes (e.g. {'x': 100, 'y': 50, 'width': 200, 'height': 100, 'fill': '#3b82f6', 'stroke': '#1e293b', 'stroke_width': 2}).
    """
    if element_type not in {"rect", "circle", "ellipse", "line", "polygon", "polyline"}:
        raise ToolError(f"Unsupported shape type: {element_type}")
    attrs = {k.replace("_", "-"): str(v) for k, v in (attributes or {}).items()}
    attrs["id"] = id
    payload = {"operation": "create-element", "element": {"tag": element_type, "attributes": attrs}}
    return dispatch_operation(payload)


class ElementSpec(BaseModel):
    """Known element fields plus arbitrary SVG attributes at the root."""

    model_config = ConfigDict(extra="allow")

    tag: str | None = None
    type: str | None = None
    id: str | None = None
    attributes: dict[str, Any] | None = None
    children: list["ElementSpec"] | None = None


class CanvasSize(BaseModel):
    width: Annotated[float, Field(gt=0, allow_inf_nan=False, description="Canvas width.")]
    height: Annotated[float, Field(gt=0, allow_inf_nan=False, description="Canvas height.")]
    unit: Annotated[
        Literal["px", "mm", "cm", "in", "pt", "pc"],
        Field(description="Canvas unit; defaults to px."),
    ] = "px"


class ElementModification(BaseModel):
    id: str
    attributes: dict[str, Any]


ElementSpec.model_rebuild()


def _normalize_element_spec(item: dict[str, Any] | ElementSpec) -> dict[str, Any]:
    """Normalize user/LLM input dictionary to standard extension format."""
    if isinstance(item, ElementSpec):
        item = item.model_dump(exclude_unset=True)
    tag = item.get("tag") or item.get("type", "")
    element_id = item.get("id")
    attributes = dict(item.get("attributes") or {})

    # Extract any root-level attributes not reserved
    reserved = {"tag", "type", "id", "attributes", "children"}
    for k, v in item.items():
        if k not in reserved:
            attr_key = k.replace("_", "-")
            attributes[attr_key] = str(v)

    if element_id:
        attributes["id"] = element_id

    spec: dict[str, Any] = {
        "tag": tag,
        "attributes": attributes,
    }
    if "children" in item:
        spec["children"] = [_normalize_element_spec(c) for c in item["children"]]
    return spec


@mcp.tool()
def create_elements(
    elements: list[ElementSpec],
    group_id: str | None = None,
    clear_canvas: bool = False,
    canvas_size: CanvasSize | None = None,
    fit_canvas: bool = False,
    fit_padding: Annotated[float, Field(ge=0, allow_inf_nan=False)] = 20.0,
    select_created: bool = False,
    return_preview: bool = False,
    preview_max_size: Annotated[int, Field(ge=1, le=4096)] = 800,
) -> str | ImageContent | list[TextContent | ImageContent]:
    """Create SVG elements in one batch. Each element needs a tag or type and may include an ID,
    an attributes object, direct SVG attributes, and nested children. Optional canvas setup,
    auto-framing, selection, and preview run in the same request.

    Args:
        elements: List of element dictionaries. Each dictionary can specify:
            - 'tag' (or 'type'): SVG element tag. The extension validates supported tags.
            - 'id': (optional) Desired element ID.
            - 'attributes': (optional) Dict of attributes (e.g. {'x': 10, 'y': 20, 'fill': 'blue'}).
            - Any direct SVG attributes are also accepted at the root, with underscores normalized to hyphens.
            - 'children': (optional) Nested list of child element dicts.
        group_id: (optional) Wrap all created elements inside a parent <g id="..."> group.
        clear_canvas: (optional) If True, removes all existing drawing elements before creating new ones.
        canvas_size: (optional) Set initial dimensions with width, height, and unit (px, mm, cm, in, pt, or pc).
        fit_canvas: (optional) If True, automatically fits page viewBox and dimensions to all elements with padding.
        fit_padding: (optional) Padding in pixels for fit_canvas (default 20.0).
        select_created: (optional) If True, selects the created elements in Inkscape's user interface.
        return_preview: (optional) If True, immediately exports and returns a PNG preview screenshot in the same MCP tool call.
        preview_max_size: (optional) Max pixel dimension for preview image (default 800).
    """
    normalized_elements = [_normalize_element_spec(elem) for elem in elements]
    if return_preview:
        _validate_preview_size(preview_max_size)
    payload: dict[str, Any] = {
        "operation": "create-elements",
        "elements": normalized_elements,
        "clear_canvas": clear_canvas,
        "fit_canvas": fit_canvas,
        "fit_padding": fit_padding,
        "select_created": select_created,
    }
    if group_id:
        payload["group_id"] = group_id
    if canvas_size:
        payload["canvas_size"] = (
            canvas_size.model_dump() if isinstance(canvas_size, CanvasSize) else canvas_size
        )

    result = dispatch_operation(payload)
    return _attach_preview(result, preview_max_size) if return_preview else result


@mcp.tool()
def create_path(
    id: str,
    d: str,
    stroke: str = "black",
    stroke_width: float = 1.0,
    fill: str = "none",
    attributes: dict[str, Any] | None = None,
) -> str | ImageContent | list[TextContent | ImageContent]:
    """Create an SVG path element with path data commands.

    Args:
        id: Unique identifier for the path element.
        d: SVG path definition data string (e.g. 'M 10 10 L 90 90 Z', 'C 20 20 40 40 50 50').
        stroke: Stroke color (hex or named color, e.g. '#22c55e', 'black').
        stroke_width: Stroke width in pixels.
        fill: Fill color or 'none'.
        attributes: Optional extra attributes (e.g. {'stroke_linecap': 'round', 'opacity': 0.8}).
    """
    attrs = {k.replace("_", "-"): str(v) for k, v in (attributes or {}).items()}
    attrs.update(
        {
            "id": id,
            "d": d,
            "stroke": stroke,
            "stroke-width": str(stroke_width),
            "fill": fill,
        }
    )
    payload = {"operation": "create-element", "element": {"tag": "path", "attributes": attrs}}
    return dispatch_operation(payload)


@mcp.tool()
def create_text(
    id: str,
    text: str,
    x: float,
    y: float,
    font_size: float = 14.0,
    fill: str = "black",
    font_family: str = "sans-serif",
    attributes: dict[str, Any] | None = None,
) -> str | ImageContent:
    """Create an SVG text element.

    Tip: For logos, wordmarks or precise kerning, prefer geometric vector paths or keep words in a single text element, as individual letter kerning depends on local OS fonts.

    Args:
        id: Unique identifier for the text element.
        text: Text string content to display.
        x: X coordinate position.
        y: Y coordinate position (baseline).
        font_size: Font size in pixels.
        fill: Text color.
        font_family: Font family name (e.g. 'sans-serif', 'Arial').
        attributes: Optional extra attributes (e.g. {'font_weight': 'bold', 'text_anchor': 'middle'}).
    """
    attrs = {k.replace("_", "-"): str(v) for k, v in (attributes or {}).items()}
    attrs.update(
        {
            "id": id,
            "text": text,
            "x": str(x),
            "y": str(y),
            "font-size": str(font_size),
            "fill": fill,
            "font-family": font_family,
        }
    )
    payload = {"operation": "create-element", "element": {"tag": "text", "attributes": attrs}}
    return dispatch_operation(payload)


@mcp.tool()
def create_group(
    id: str,
    children_ids: list[str] | None = None,
) -> str:
    """Create an SVG group (<g>), optionally moving contiguous siblings into it.

    Existing elements must share a parent and form a contiguous range in paint order.
    Documents containing CSS stylesheets are rejected because regrouping can change CSS matching.

    Args:
        id: Unique identifier for the group.
        children_ids: Optional IDs of existing contiguous sibling elements to move into the group.
    """
    payload = {
        "operation": "create-group",
        "attributes": {"id": id, "children_ids": children_ids or []},
    }
    return str(dispatch_operation(payload))


@mcp.tool()
def modify_elements(
    elements: list[ElementModification],
    fit_canvas: bool = False,
    fit_padding: Annotated[float, Field(ge=0, allow_inf_nan=False)] = 20.0,
    return_preview: bool = False,
    preview_max_size: Annotated[int, Field(ge=1, le=4096)] = 800,
) -> str | ImageContent | list[TextContent | ImageContent]:
    """Atomically modify non-ID attributes of existing elements in a batch. Each item needs a
    non-empty ID and a non-empty attributes object.

    Args:
        elements: Non-empty list of objects, each with an existing 'id' and a non-empty 'attributes' object.
        fit_canvas: Automatically fit page viewBox and dimensions to all elements after modification.
        fit_padding: Padding in pixels when fit_canvas is True (default 20.0).
        return_preview: Immediately capture and return a PNG preview screenshot in the same MCP tool call.
        preview_max_size: Maximum pixel dimension for preview image (default 800).
    """
    if return_preview:
        _validate_preview_size(preview_max_size)
    if not isinstance(elements, list) or not elements:
        raise ToolError("elements must be a non-empty list")

    items_to_update = []
    for item in elements:
        if isinstance(item, ElementModification):
            item = item.model_dump(exclude_unset=True)
        if not isinstance(item, dict):
            raise ToolError("Each element must be an object with id and attributes")
        elem_id = item.get("id")
        raw_attrs = item.get("attributes", {})
        if not isinstance(elem_id, str) or not elem_id:
            raise ToolError("Each element must have a non-empty id")
        if not isinstance(raw_attrs, dict) or not raw_attrs:
            raise ToolError(f"Element '{elem_id}' must have a non-empty attributes object")
        if any(not isinstance(key, str) for key in raw_attrs):
            raise ToolError(f"Element '{elem_id}' attribute names must be strings")
        clean_attrs = {k.replace("_", "-"): str(v) for k, v in raw_attrs.items()}
        items_to_update.append({"id": elem_id, "attributes": clean_attrs})

    payload = {"operation": "modify-elements", "attributes": {"elements": items_to_update}}
    result = dispatch_operation(payload)

    if fit_canvas:
        try:
            fit_result = fit_canvas_to_elements(padding=fit_padding)
            result = f"{result}\n{fit_result}"
        except Exception as e:
            logger.warning("Canvas fitting failed after modification was applied: %s", e)
            result = f"{result}\nWarning: the modification was applied, but canvas fitting failed."

    return _attach_preview(str(result), preview_max_size) if return_preview else str(result)


@mcp.tool()
def delete_elements(
    ids: list[str],
    return_preview: bool = False,
    preview_max_size: Annotated[int, Field(ge=1, le=4096)] = 800,
) -> str | ImageContent | list[TextContent | ImageContent]:
    """Delete one or more existing SVG elements by ID in a single batch. Pass IDs as a list; use
    a one-item list to delete one element.

    Args:
        ids: Non-empty list of existing element IDs. Use a one-item list to delete one element.
        return_preview: Immediately capture and return a PNG preview screenshot in the same MCP tool call.
        preview_max_size: Maximum pixel dimension for preview image (default 800).
    """
    if return_preview:
        _validate_preview_size(preview_max_size)
    if (
        not isinstance(ids, list)
        or not ids
        or any(not isinstance(item, str) or not item for item in ids)
    ):
        raise ToolError("ids must be a non-empty list of non-empty strings")

    payload = {"operation": "delete-elements", "attributes": {"ids": ids}}
    result = dispatch_operation(payload)

    return _attach_preview(str(result), preview_max_size) if return_preview else str(result)


@mcp.tool()
def path_boolean_operation(
    operation: Literal[
        "union", "difference", "intersection", "exclusion", "division", "combine", "break-apart"
    ],
    element_ids: list[str],
    return_preview: bool = False,
    preview_max_size: Annotated[int, Field(ge=1, le=4096)] = 800,
) -> str | ImageContent | list[TextContent | ImageContent]:
    """Perform native boolean operations on existing SVG paths. All operations need two paths
    except combine and break-apart, which accept one. Difference follows canvas paint order.

    Args:
        operation: Boolean operation to apply:
            - 'union': Fuse paths into a single shape.
            - 'difference': Subtract paths according to Inkscape canvas paint order. The IDs identify participants; their list order does not set subtraction order.
            - 'intersection': Keep only overlapping common areas.
            - 'exclusion': Keep areas where an odd number of paths overlap.
            - 'division': Cut the first path with the contours of the other paths.
            - 'combine': Combine multiple paths into a single compound path without merging overlapping fills.
            - 'break-apart': Break a compound path into its constituent sub-paths.
        element_ids: Existing path IDs. All operations except combine and break-apart need at least two; those two need one. Duplicate IDs are rejected.
        return_preview: Immediately capture and return a PNG preview screenshot in the same MCP tool call.
        preview_max_size: Maximum pixel dimension for preview image (default 800).
    """
    if return_preview:
        _validate_preview_size(preview_max_size)
    valid_ops = {
        "union": "path-union",
        "difference": "path-difference",
        "intersection": "path-intersection",
        "exclusion": "path-exclusion",
        "division": "path-division",
        "combine": "path-combine",
        "break-apart": "path-break-apart",
    }
    op_clean = operation.lower().strip()
    if op_clean not in valid_ops:
        raise ToolError(
            f"Invalid operation '{operation}'. Supported: {', '.join(valid_ops.keys())}"
        )

    minimum_ids = 1 if op_clean in ("break-apart", "combine") else 2
    if len(element_ids) < minimum_ids:
        raise ToolError(f"Operation '{operation}' requires at least {minimum_ids} element ID(s)")
    if any(not isinstance(element_id, str) or not element_id for element_id in element_ids):
        raise ToolError("element_ids must contain non-empty strings")
    if len(element_ids) != len(set(element_ids)):
        raise ToolError("element_ids must not contain duplicates")

    conn = get_inkscape_connection()
    if not conn.is_available():
        raise ToolError("Inkscape D-Bus service is not available")

    for element_id in element_ids:
        info = conn.execute_operation(
            {"operation": "get-info-by-id", "attributes": {"id": element_id}}
        )
        element = info.get("data", {})
        if info.get("status") != "success":
            raise ToolError(element.get("error", f"Element '{element_id}' was not found"))
        if element.get("tag") != "path":
            raise ToolError(f"Element '{element_id}' is not an SVG path")

    previous = conn.execute_operation({"operation": "get-selection", "attributes": {}})
    previous_ids = [
        element.get("id")
        for element in previous.get("data", {}).get("elements", [])
        if isinstance(element.get("id"), str)
    ]

    action_name = valid_ops[op_clean]

    with conn._lock:
        if not conn.activate_action("select-clear", "[]"):
            raise ToolError("Could not clear Inkscape selection before the boolean operation")
        for eid in element_ids:
            if not conn.activate_action(
                "select-by-id", f"[<{json.dumps(eid, ensure_ascii=False)}>]"
            ):
                conn.activate_action("select-clear", "[]")
                for previous_id in previous_ids:
                    conn.activate_action(
                        "select-by-id", f"[<{json.dumps(previous_id, ensure_ascii=False)}>]"
                    )
                raise ToolError(f"Could not select element '{eid}'")
        success = conn.activate_action(action_name, "[]")

    if not success:
        raise ToolError(
            f"Inkscape did not confirm '{action_name}'; the document outcome may be unknown"
        )

    result = f"Operation '{operation}' executed on {', '.join(element_ids)}"
    if op_clean == "difference":
        result += (
            ". Inkscape subtracts according to canvas paint order; IDs do not set subtraction order"
        )
    return _attach_preview(result, preview_max_size) if return_preview else result


@mcp.tool()
def apply_mask(
    target_id: str,
    mask_id: str | None = None,
    mode: Literal["clip", "luminance"] = "clip",
    remove: bool = False,
    return_preview: bool = False,
    preview_max_size: Annotated[int, Field(ge=1, le=4096)] = 800,
) -> str | ImageContent | list[TextContent | ImageContent]:
    """Apply or remove a clip or luminance mask on an element or group. Applying moves the stencil
    into definitions. Transformed targets or stencils, inherited or class-based styles, and CSS
    stylesheets are unsupported.

    Args:
        target_id: ID of the element or group to be clipped or masked.
        mask_id: ID of the element serving as the clipping shape or mask stencil (required unless remove=True).
        mode: Masking mode:
            - 'clip': Hard boundary clipping via <clipPath>. Areas outside the mask shape are hidden (default).
            - 'luminance': Smooth transparency masking via <mask>. Grayscale luminance values determine opacity.
        remove: If True, removes any existing clip-path and mask attributes from the target element.
        return_preview: Immediately capture and return a PNG preview screenshot in the same MCP tool call.
        preview_max_size: Maximum pixel dimension for preview image (default 800).
    """
    if return_preview:
        _validate_preview_size(preview_max_size)
    clean_mode = mode.lower().strip()
    if clean_mode not in ("clip", "luminance"):
        raise ToolError(f"Invalid mode '{mode}'. Supported: 'clip', 'luminance'")

    if not remove and not mask_id:
        raise ToolError("mask_id is required when remove=False")
    payload = {
        "operation": "apply-mask",
        "attributes": {
            "target_id": target_id,
            "mask_id": mask_id,
            "mode": clean_mode,
            "remove": remove,
        },
    }
    result = dispatch_operation(payload)

    return _attach_preview(str(result), preview_max_size) if return_preview else str(result)


@mcp.tool()
def get_document_info() -> str:
    """Get metadata about the current document: dimensions, viewBox, and element counts."""
    payload = {"operation": "get-info", "attributes": {}}
    return str(dispatch_operation(payload))


@mcp.tool()
def list_elements(
    limit: Annotated[int, Field(ge=1, le=500)] = 100,
    offset: Annotated[int, Field(ge=0)] = 0,
) -> str:
    """List a page of SVG elements in document order, including definitions. Summaries include
    tag, ID, Inkscape label, parent ID, and depth. Limit is 1 to 500; offset starts at zero.
    """
    if type(limit) is not int or not 1 <= limit <= 500:
        raise ToolError("limit must be an integer from 1 to 500")
    if type(offset) is not int or offset < 0:
        raise ToolError("offset must be a non-negative integer")
    return str(
        dispatch_operation(
            {"operation": "list-elements", "attributes": {"limit": limit, "offset": offset}}
        )
    )


@mcp.tool()
def get_element_info(element_id: str) -> str:
    """Get the complete attributes and parsed inline style of one element by its ID."""
    if not isinstance(element_id, str) or not element_id:
        raise ToolError("element_id must be a non-empty string")
    return str(
        dispatch_operation({"operation": "get-info-by-id", "attributes": {"id": element_id}})
    )


@mcp.tool()
def get_selection() -> str:
    """Get information about elements currently selected by the user in the Inkscape UI."""
    payload = {"operation": "get-selection", "attributes": {}}
    return str(dispatch_operation(payload))


@mcp.tool()
def fit_canvas_to_elements(
    padding: Annotated[float, Field(ge=0, allow_inf_nan=False)] = 20.0,
) -> str:
    """Automatically adjust the SVG viewBox and page dimensions to frame all drawing elements with padding.

    Tip: Best used on geometric shapes and paths; long text elements with wide letter-spacing may overestimate canvas bounds.

    Args:
        padding: Padding in pixels around the bounding box of elements (default 20.0).
    """
    payload = {"operation": "fit-canvas", "attributes": {"padding": padding}}
    return str(dispatch_operation(payload))


@mcp.tool()
def set_canvas_size(
    width: Annotated[float, Field(gt=0, allow_inf_nan=False)],
    height: Annotated[float, Field(gt=0, allow_inf_nan=False)],
    unit: Literal["px", "mm", "cm", "in", "pt", "pc"] = "px",
) -> str:
    """Set canvas width, height, and viewBox using px, mm, cm, in, pt, or pc units.

    Args:
        width: Canvas width.
        height: Canvas height.
        unit: Canvas measurement unit: px, mm, cm, in, pt, or pc (default px).
    """
    payload = {
        "operation": "set-canvas",
        "attributes": {"width": width, "height": height, "unit": unit},
    }
    return str(dispatch_operation(payload))


@mcp.tool()
def clear_canvas() -> str:
    """Clear drawing elements while preserving definitions, named view, metadata, title, description, and styles."""
    return str(dispatch_operation({"operation": "clear-canvas", "attributes": {}}))


@mcp.tool()
def export_preview(max_size: Annotated[int, Field(ge=1, le=4096)] = 800) -> ImageContent:
    """Export and return a PNG screenshot image of the current Inkscape document.

    Uses native Inkscape D-Bus actions for sub-second export.

    Args:
        max_size: Maximum pixel dimension for the exported image (default 800).
    """
    _validate_preview_size(max_size)
    preview = get_inkscape_connection().export_native_preview(max_size=max_size)
    if preview is None:
        raise ToolError("Native Inkscape preview could not be prepared or exported")
    return preview


def main():
    """Run the Inkscape MCP server"""
    parser = argparse.ArgumentParser(description="Tinta MCP server (structured SVG operations)")
    parser.parse_args()
    logger.info("Starting Tinta MCP Server...")
    mcp.run()


if __name__ == "__main__":
    main()
