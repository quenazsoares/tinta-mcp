"""Check bundled assets and installation without touching the user's Inkscape profile."""

import json
import sys
import tomllib
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from tintamcp import installer
from tintamcp.agents.catalog import AgentContext


def main() -> None:
    assets = Path(installer.__file__).parent / "assets"
    extension = assets / "extension"
    skill = assets / "skills" / "tinta-designer"
    if not (skill / "SKILL.md").is_file():
        raise RuntimeError("Installed package is missing the designer skill")
    with TemporaryDirectory() as directory:
        root = Path(directory)
        profile = root / "extensions"
        profile.mkdir()
        home = root / "home"
        (home / ".codex").mkdir(parents=True)
        skills = home / ".agents" / "skills"
        antigravity = home / ".gemini" / "config" / "mcp_config.json"
        antigravity.parent.mkdir(parents=True)
        original = b'{"mcpServers":{"other":{"command":"example"}}}\n'
        antigravity.write_bytes(original)
        with (
            patch.object(installer, "get_inkscape_extensions_dir", return_value=profile),
            patch.object(
                installer, "get_agent_context", return_value=AgentContext(home, "linux", {})
            ),
        ):
            installer.run_installer(auto_yes=True)
            (skills / "tinta-designer" / "SKILL.md").write_text("old skill", encoding="utf-8")
            notes = skills / "tinta-designer" / "personal.md"
            notes.write_text("keep these notes", encoding="utf-8")
            with (
                patch.object(sys.stdin, "isatty", return_value=True),
                patch("builtins.input", return_value="y"),
            ):
                installer.run_installer(auto_yes=True, configure_mcp=True)
                backups_before = set(home.rglob("*.bak-*"))
                installer.run_installer(auto_yes=True, configure_mcp=True)
                if set(home.rglob("*.bak-*")) != backups_before:
                    raise RuntimeError("Repeated configuration created extra backups")
            if notes.read_text(encoding="utf-8") != "keep these notes":
                raise RuntimeError("Skill update removed additional files")
        codex = tomllib.loads((home / ".codex/config.toml").read_text(encoding="utf-8"))
        if codex["mcp_servers"]["tinta"]["args"] != ["tinta-mcp"]:
            raise RuntimeError("Installed wheel failed to configure Codex")
        configured = json.loads(antigravity.read_bytes())
        if configured["mcpServers"]["other"]["command"] != "example":
            raise RuntimeError("MCP configuration changed another server")
        if next(antigravity.parent.glob("mcp_config.json.bak-*")).read_bytes() != original:
            raise RuntimeError("MCP configuration backup differs from original")
        expected = [
            "tinta_mcp.py",
            "tinta_mcp.inx",
            "tinta_mcp_ops/__init__.py",
            "tinta_mcp_ops/common.py",
            "tinta_mcp_ops/document_operations.py",
            "tinta_mcp_ops/element_mapping.py",
        ]
        for name in expected:
            if (profile / name).read_bytes() != (extension / name).read_bytes():
                raise RuntimeError(f"Extension installation differs from package: {name}")
        if (skills / "tinta-designer" / "SKILL.md").read_bytes() != (
            skill / "SKILL.md"
        ).read_bytes():
            raise RuntimeError("Installed skill differs from package")
    print("Installed package assets, extension/skills and MCP configuration passed")


if __name__ == "__main__":
    main()
