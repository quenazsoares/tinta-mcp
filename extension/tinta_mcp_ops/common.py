import getpass
import os
import platform
from pathlib import Path
from typing import Any


def get_tinta_ipc_dir() -> Path:
    """Return the designated user-isolated directory for Tinta MCP IPC files:
    - Linux: private $XDG_RUNTIME_DIR/tintamcp/ or /tmp/tintamcp-<uid>/.
    - Windows: %LOCALAPPDATA%\\Temp\\tintamcp\\<username>\\.
    """
    try:
        username = getpass.getuser()
    except Exception:
        username = os.environ.get("USERNAME") or os.environ.get("USER") or "default"

    safe_username = "".join(c for c in username if c.isalnum() or c in ("-", "_")) or "user"

    if platform.system() == "Windows":
        local_app_data = os.environ.get("LOCALAPPDATA")
        if not local_app_data:
            raise OSError("LOCALAPPDATA is required for the Windows Tinta IPC directory")
        local_root = Path(local_app_data).resolve()
        base = local_root / "Temp" / "tintamcp" / safe_username
        base.mkdir(parents=True, exist_ok=True)
        resolved = base.resolve()
        if base.is_symlink() or local_root not in resolved.parents:
            raise OSError(f"Unsafe Tinta IPC directory: {base}")
    else:
        uid = os.getuid()
        runtime_dir = os.environ.get("XDG_RUNTIME_DIR")
        runtime_path = Path(runtime_dir) if runtime_dir else None
        if runtime_path is not None:
            try:
                info = runtime_path.lstat()
                if (
                    runtime_path.is_symlink()
                    or not runtime_path.is_dir()
                    or info.st_uid != uid
                    or info.st_mode & 0o077
                ):
                    runtime_path = None
            except OSError:
                runtime_path = None
        base = (runtime_path / "tintamcp") if runtime_path else Path(f"/tmp/tintamcp-{uid}")
        base.mkdir(mode=0o700, parents=True, exist_ok=True)
        info = base.lstat()
        if not base.is_dir() or base.is_symlink() or info.st_uid != uid or info.st_mode & 0o077:
            raise OSError(f"Unsafe Tinta IPC directory: {base}")

    return base


def create_success_response(message: str, **data) -> dict[str, Any]:
    """Create a standardized success response"""
    response_data = {"message": message}
    response_data.update(data)
    return {
        "status": "success",
        "data": response_data,
    }


def create_error_response(error_message: str, **data) -> dict[str, Any]:
    """Create a standardized error response"""
    response_data = {"error": error_message}
    response_data.update(data)
    return {
        "status": "error",
        "data": response_data,
    }


def get_element_info_data(element) -> dict[str, Any]:
    """Extract comprehensive element information"""
    element_info: dict[str, Any] = {
        "id": element.get("id", "no-id"),
        "tag": element.tag.split("}")[-1],  # Remove namespace
        "label": element.get("{http://www.inkscape.org/namespaces/inkscape}label", None),
    }

    # Get all attributes
    attributes = {}
    for key, value in element.attrib.items():
        clean_key = key.split("}")[-1]  # Remove namespace prefixes
        attributes[clean_key] = value

    element_info["attributes"] = attributes

    # Parse style attributes
    style_info = {}
    style_attr = element.get("style", "")
    if style_attr:
        for style_part in style_attr.split(";"):
            if ":" in style_part:
                key, value = style_part.split(":", 1)
                style_info[key.strip()] = value.strip()

    if style_info:
        element_info["style"] = style_info

    return element_info
