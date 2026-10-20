"""Prepare minimal MCP configuration updates in memory, without writes."""

import json
import ntpath
from collections.abc import MutableMapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

import tomlkit
from tomlkit.exceptions import TOMLKitError

from .detection import DetectedTarget


class _ConfigurationConflict(ValueError):
    """A structural diagnosis that contains no configuration values."""


@dataclass(frozen=True)
class McpServerSpec:
    command: str
    args: tuple[str, ...]


@dataclass(frozen=True)
class ConfigChange:
    path: Path
    before: bytes | None
    after: bytes | None
    status: Literal["create", "update", "unchanged", "conflict"]
    preview: str


def _unique_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result = {}
    for key, value in pairs:
        if key in result:
            raise _ConfigurationConflict("Duplicate JSON key")
        result[key] = value
    return result


def _reject_constant(value: str) -> None:
    raise _ConfigurationConflict("Nonstandard JSON number")


def _equivalent_command(current: object, desired: str) -> bool:
    # A bare uvx denotes the executable resolved by the installer. Distinct
    # absolute paths are never equated just because they have the same basename.
    return (
        current == desired
        or current in ("uvx", "uvx.exe")
        and ntpath.basename(desired).lower() in ("uvx", "uvx.exe")
    )


def prepare_mcp_change(
    target: DetectedTarget, before: bytes | None, server: McpServerSpec
) -> ConfigChange:
    try:
        if target.capability != "mcp" or target.adapter is None:
            raise _ConfigurationConflict("Not an MCP configuration target")
        bom = before is not None and before.startswith(b"\xef\xbb\xbf")
        source = before.decode("utf-8-sig") if before is not None else ""
        is_json = target.adapter == "json-mcpServers"
        if is_json:
            document = (
                json.loads(
                    source, object_pairs_hook=_unique_object, parse_constant=_reject_constant
                )
                if source.strip()
                else {}
            )
            root = "mcpServers"
        elif target.adapter == "toml-codex":
            document = tomlkit.parse(source)
            root = "mcp_servers"
        else:
            raise _ConfigurationConflict("Unknown configuration adapter")
        if not isinstance(document, MutableMapping):
            raise _ConfigurationConflict("Configuration root must be an object")
        if root not in document:
            document[root] = {} if is_json else tomlkit.table()
        servers = document[root]
        if not isinstance(servers, MutableMapping):
            raise _ConfigurationConflict("Servers must be an object")
        if "tinta" not in servers:
            servers["tinta"] = {} if is_json else tomlkit.table()
        entry = servers["tinta"]
        if not isinstance(entry, MutableMapping):
            raise _ConfigurationConflict("Tinta must be an object")
        if any(k in entry for k in ("url", "serverUrl", "httpUrl", "type", "transport")):
            raise _ConfigurationConflict("Incompatible transport")
        if "command" in entry and not isinstance(entry["command"], str):
            raise _ConfigurationConflict("Invalid command")
        if "args" in entry and (
            not isinstance(entry["args"], list)
            or not all(isinstance(x, str) for x in entry["args"])
        ):
            raise _ConfigurationConflict("Invalid arguments")
        if "enabled" in entry and not isinstance(entry["enabled"], bool):
            raise _ConfigurationConflict("Invalid enabled flag")
        if "disabled" in entry and not isinstance(entry["disabled"], bool):
            raise _ConfigurationConflict("Invalid disabled flag")
        enabled = entry.get("enabled") is not False and entry.get("disabled") is not True
        if (
            _equivalent_command(entry.get("command"), server.command)
            and entry.get("args") == list(server.args)
            and enabled
        ):
            return ConfigChange(
                target.path, before, before, "unchanged", "Tinta is already configured."
            )
        entry["command"] = server.command
        entry["args"] = list(server.args)
        if entry.get("enabled") is False:
            entry["enabled"] = True
        if entry.get("disabled") is True:
            entry["disabled"] = False
        result = (
            json.dumps(document, ensure_ascii=False, indent=2, allow_nan=False) + "\n"
            if is_json
            else tomlkit.dumps(document)
        )
        # Preserve the original newline convention and optional UTF-8 BOM.
        result = result.replace("\r\n", "\n")
        if "\r\n" in source:
            result = result.replace("\n", "\r\n")
        after = (b"\xef\xbb\xbf" if bom else b"") + result.encode("utf-8")
        if is_json:
            json.loads(after.decode("utf-8-sig"))
        else:
            tomlkit.parse(after.decode("utf-8-sig"))
        # Describe only controlled fields; never echo the original entry, env,
        # parse errors, or full-file diffs, which can contain credentials.
        preview = (
            f"{'Create' if before is None else 'Update'} {root}.tinta\n"
            f"  command = {json.dumps(server.command)}\n"
            f"  args = {json.dumps(list(server.args))}\n"
            "  Existing command and arguments will be replaced if different.\n"
            "  Other compatible fields and other servers will be preserved."
        )
        if not enabled:
            preview += (
                "\n  Enable the currently disabled Tinta entry (enabled = true / disabled = false)."
            )
        if is_json:
            preview += "\n  The JSON file will be reformatted using two-space indentation."
        return ConfigChange(
            target.path, before, after, "create" if before is None else "update", preview
        )
    except _ConfigurationConflict as error:
        reason = str(error)
    except json.JSONDecodeError:
        reason = "Invalid JSON (comments and trailing commas are not supported)"
    except TOMLKitError:
        reason = "Invalid TOML"
    except UnicodeError:
        reason = "Configuration must use UTF-8 encoding"
    except (ValueError, TypeError):
        reason = "Unsupported configuration value"
    return ConfigChange(
        target.path,
        before,
        None,
        "conflict",
        f"{reason}. Configure Tinta manually; the file is unchanged.",
    )
