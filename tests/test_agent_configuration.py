import json
import tomllib
from pathlib import Path

import pytest

from tintamcp.agents.catalog import Adapter
from tintamcp.agents.configuration import McpServerSpec, prepare_mcp_change
from tintamcp.agents.detection import DetectedTarget


def target(adapter: Adapter = "json-mcpServers"):
    return DetectedTarget(Path("config"), "mcp", ("test",), ("Test",), "test", adapter)


SERVER = McpServerSpec("C:/tools/uvx.exe", ("tinta-mcp",))


@pytest.mark.parametrize("adapter", ["json-mcpServers", "toml-codex"])
def test_create_and_repeat_are_idempotent(adapter):
    change = prepare_mcp_change(target(adapter), None, SERVER)
    assert change.status == "create"
    assert change.after is not None
    second = prepare_mcp_change(target(adapter), change.after, SERVER)
    assert second.status == "unchanged"
    assert second.after == change.after


def test_json_preserves_other_servers_extra_fields_and_redacts_secrets():
    before = json.dumps(
        {
            "unrelated": 42,
            "mcpServers": {
                "private": {"env": {"TOKEN": "other-secret"}},
                "tinta": {
                    "command": "uvx",
                    "args": ["--from", "https://test.pypi.org/secret", "tinta-mcp"],
                    "env": {"API_KEY": "private-secret"},
                    "timeout": 77,
                },
            },
        }
    ).encode()
    change = prepare_mcp_change(target(), before, SERVER)
    assert change.status == "update"
    assert change.after is not None
    result = json.loads(change.after)
    assert result["unrelated"] == 42
    assert result["mcpServers"]["private"]["env"]["TOKEN"] == "other-secret"
    assert result["mcpServers"]["tinta"]["env"]["API_KEY"] == "private-secret"
    assert result["mcpServers"]["tinta"]["timeout"] == 77
    assert result["mcpServers"]["tinta"]["args"] == ["tinta-mcp"]
    assert "secret" not in change.preview
    assert "reformat" in change.preview.lower()


def test_toml_keeps_comments_nested_tables_and_unrelated_settings():
    before = b"""# my comment\r\nmodel = "example"\r\n[mcp_servers.tinta]\r\ncommand = "uvx"\r\nargs = ["--from", "old", "tinta-mcp"]\r\nenabled = false # intentional\r\nstartup_timeout_sec = 60\r\n[mcp_servers.tinta.env]\r\nTOKEN = "secret-token"\r\n"""
    change = prepare_mcp_change(target("toml-codex"), before, SERVER)
    assert change.status == "update"
    assert change.after is not None
    assert b"# my comment\r\n" in change.after
    assert b"# intentional" in change.after
    data = tomllib.loads(change.after.decode())
    assert data["model"] == "example"
    assert data["mcp_servers"]["tinta"]["enabled"] is True
    assert data["mcp_servers"]["tinta"]["startup_timeout_sec"] == 60
    assert data["mcp_servers"]["tinta"]["env"]["TOKEN"] == "secret-token"
    assert "enable" in change.preview.lower()
    assert "secret-token" not in change.preview


def test_uvx_name_matches_resolved_executable():
    before = b'{"mcpServers":{"tinta":{"command":"uvx","args":["tinta-mcp"]}}}'
    change = prepare_mcp_change(target(), before, SERVER)
    assert change.status == "unchanged"
    assert change.after == before


@pytest.mark.parametrize(
    "before",
    [
        b"[]",
        b'{"mcpServers":[]}',
        b'{"mcpServers":{"tinta":false}}',
        b"{// comment\n}",
        b'{"mcpServers":{"tinta":{"url":"https://remote"}}}',
        b'{"mcpServers":{"tinta":{"command":"uvx","args":"wrong"}}}',
        b'{"mcpServers":{},"mcpServers":{}}',
    ],
)
def test_invalid_or_incompatible_json_is_a_conflict(before):
    change = prepare_mcp_change(target(), before, SERVER)
    assert change.status == "conflict"
    assert change.after is None


def test_invalid_toml_does_not_expose_source_or_parser_excerpt():
    change = prepare_mcp_change(target("toml-codex"), b'TOKEN="mysecret"\n[broken', SERVER)
    assert change.status == "conflict"
    assert "mysecret" not in change.preview


def test_utf8_bom_unicode_and_newlines():
    before = '\ufeff{\r\n  "name": "ação"\r\n}\r\n'.encode()
    change = prepare_mcp_change(target(), before, SERVER)
    assert change.after is not None
    assert change.after.startswith(b"\xef\xbb\xbf")
    assert b"\r\n" in change.after
    assert json.loads(change.after.decode("utf-8-sig"))["name"] == "ação"


def test_antigravity_disabled_flag_is_enabled_explicitly():
    before = b'{"mcpServers":{"tinta":{"command":"uvx","args":["tinta-mcp"],"disabled":true}}}'
    change = prepare_mcp_change(target(), before, SERVER)
    assert change.status == "update"
    assert change.after is not None
    assert json.loads(change.after)["mcpServers"]["tinta"]["disabled"] is False
    assert "disabled = false" in change.preview


@pytest.mark.parametrize("number", [b"NaN", b"1e400"])
def test_nonstandard_json_numbers_are_conflicts(number):
    change = prepare_mcp_change(target(), b'{"other":' + number + b"}", SERVER)
    assert change.status == "conflict"
