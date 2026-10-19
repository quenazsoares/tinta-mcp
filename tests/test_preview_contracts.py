"""Native preview preparation and image validation contracts."""

import json
import struct
import zlib
from unittest.mock import patch

from tintamcp.server import InkscapeConnection, _is_complete_png


def _chunk(kind: bytes, content: bytes) -> bytes:
    return (
        struct.pack(">I", len(content))
        + kind
        + content
        + struct.pack(">I", zlib.crc32(kind + content) & 0xFFFFFFFF)
    )


def _png(width: int = 100, height: int = 63) -> bytes:
    header = struct.pack(">IIBBBBB", width, height, 8, 0, 0, 0, 0)
    pixels = zlib.compress(b"\x00" * (width + 1) * height)
    return (
        b"\x89PNG\r\n\x1a\n"
        + _chunk(b"IHDR", header)
        + _chunk(b"IDAT", pixels)
        + _chunk(b"IEND", b"")
    )


def _document_info():
    return {
        "status": "success",
        "data": {"dimensions": {"width": "320", "height": "200"}},
    }


def test_native_preview_stops_before_export_when_preparation_fails(tmp_path):
    connection = InkscapeConnection()
    activated = []

    def activate(name, _parameter):
        activated.append(name)
        return name != "export-filename"

    with (
        patch.object(connection, "execute_operation", return_value=_document_info()),
        patch.object(connection, "activate_action", side_effect=activate),
        patch("tintamcp.server.get_tinta_ipc_dir", return_value=tmp_path),
    ):
        assert connection.export_native_preview() is None
    assert "export-do" not in activated


def test_native_preview_escapes_filename_and_sets_pixel_limit(tmp_path):
    ipc_dir = tmp_path / "O'Neil"
    ipc_dir.mkdir()
    connection = InkscapeConnection()
    actions = []

    def activate(name, parameter):
        actions.append((name, parameter))
        if name == "export-do":
            filename = next(value for action, value in actions if action == "export-filename")
            target = json.loads(filename[2:-2])
            with open(target, "wb") as stream:
                stream.write(_png())
        return True

    with (
        patch.object(connection, "execute_operation", return_value=_document_info()),
        patch.object(connection, "activate_action", side_effect=activate),
        patch("tintamcp.server.get_tinta_ipc_dir", return_value=ipc_dir),
    ):
        result = connection.export_native_preview(max_size=100)
    assert result is not None and result.mime_type == "image/png"
    filename = next(value for action, value in actions if action == "export-filename")
    assert json.loads(filename[2:-2]).startswith(str(ipc_dir).replace("\\", "/"))
    assert ("export-width", "[<100>]") in actions


def test_native_preview_rejects_unbounded_size_without_ipc_or_actions():
    connection = InkscapeConnection()
    with (
        patch.object(connection, "execute_operation", side_effect=AssertionError),
        patch.object(connection, "activate_action", side_effect=AssertionError),
    ):
        assert connection.export_native_preview(max_size=5000) is None


def test_png_validation_rejects_truncation_and_bad_chunk_crc():
    valid = _png()
    assert _is_complete_png(valid)
    assert not _is_complete_png(valid[:-8])
    corrupted = bytearray(valid)
    corrupted[20] ^= 0xFF
    assert not _is_complete_png(bytes(corrupted))
