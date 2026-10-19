"""
Installer module for Tinta MCP.

Installs the Inkscape extension and optionally the tinta-designer skill
directly from the installed Python package into the user's environment.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path


def get_inkscape_extensions_dir() -> Path | None:
    """Locate the Inkscape user extensions directory."""
    # 1. Try running inkscape / inkscape.com --user-data-directory
    inkscape_bin = None
    if sys.platform == "win32":
        candidates = [
            Path(r"C:\Program Files\Inkscape\bin\inkscape.com"),
            Path(r"C:\Program Files\Inkscape\inkscape.com"),
            Path(r"C:\Program Files (x86)\Inkscape\bin\inkscape.com"),
        ]
        custom_cmd = os.environ.get("INKSCAPE_COMMAND")
        if custom_cmd:
            custom_path = Path(custom_cmd)
            if custom_path.suffix.lower() == ".exe":
                com_path = custom_path.with_name("inkscape.com")
                if com_path.is_file():
                    candidates.insert(0, com_path)
            candidates.insert(0, custom_path)

        for candidate in candidates:
            if candidate.is_file():
                inkscape_bin = str(candidate)
                break
    else:
        inkscape_bin = shutil.which("inkscape")

    if inkscape_bin:
        try:
            res = subprocess.run(
                [inkscape_bin, "--user-data-directory"],
                capture_output=True,
                text=True,
                timeout=5,
            )
            if res.returncode == 0 and res.stdout.strip():
                lines = [line.strip() for line in res.stdout.strip().splitlines() if line.strip()]
                if lines:
                    user_dir = Path(lines[-1])
                    ext_dir = user_dir / "extensions"
                    ext_dir.mkdir(parents=True, exist_ok=True)
                    return ext_dir
        except Exception:
            pass

    # 2. Fallback to standard platform locations
    if sys.platform == "win32":
        appdata = os.environ.get("APPDATA")
        if appdata:
            ext_dir = Path(appdata) / "inkscape" / "extensions"
            ext_dir.mkdir(parents=True, exist_ok=True)
            return ext_dir
    else:
        ext_dir = Path.home() / ".config" / "inkscape" / "extensions"
        ext_dir.mkdir(parents=True, exist_ok=True)
        return ext_dir

    return None


def get_asset_dir(asset_name: str) -> Path | None:
    """Locate an asset directory (embedded in package or from source checkout)."""
    # 1. Embedded inside wheel / package
    pkg_assets = Path(__file__).parent / "assets" / asset_name
    if pkg_assets.exists():
        return pkg_assets

    # 2. Source tree fallback (running from git repository)
    repo_root = Path(__file__).resolve().parent.parent
    if asset_name == "extension":
        src_path = repo_root / "extension"
        if src_path.exists():
            return src_path
    elif asset_name == "skills/tinta-designer":
        src_path = repo_root / ".agents" / "skills" / "tinta-designer"
        if src_path.exists():
            return src_path

    return None


def find_agent_skills_dirs() -> list[Path]:
    """Find directories where AI agent skills are stored."""
    candidates = [
        Path.home() / ".gemini" / "config" / "skills",
        Path.home() / ".claude" / "skills",
        Path.home() / ".cursor" / "skills",
    ]
    return [p for p in candidates if p.parent.exists()]


def run_installer(auto_yes: bool = False) -> None:
    """Run the interactive or automated Tinta installation."""
    print("=" * 60)
    print(" Tinta MCP — Installation")
    print("=" * 60)
    print()

    # Step 1: Install Inkscape Extension
    print("[1/2] Locating Inkscape extensions directory...")
    ext_dir = get_inkscape_extensions_dir()
    if not ext_dir:
        print("Error: Could not locate Inkscape user extensions directory.")
        print("Please ensure Inkscape is installed.")
        sys.exit(1)

    print(f"  Target: {ext_dir}")
    ext_assets = get_asset_dir("extension")
    if not ext_assets:
        print("Error: Extension asset files not found in package.")
        sys.exit(1)

    # Clean legacy files
    for legacy in ["inkscape_mcp.py", "inkscape_mcp.inx"]:
        legacy_file = ext_dir / legacy
        if legacy_file.is_file():
            legacy_file.unlink()

    legacy_dir = ext_dir / "inkmcp"
    if legacy_dir.is_dir():
        shutil.rmtree(legacy_dir, ignore_errors=True)

    # Copy extension files
    shutil.copy2(ext_assets / "tinta_mcp.py", ext_dir / "tinta_mcp.py")
    shutil.copy2(ext_assets / "tinta_mcp.inx", ext_dir / "tinta_mcp.inx")

    ops_src = ext_assets / "ops"
    ops_dst = ext_dir / "ops"
    if ops_dst.exists():
        shutil.rmtree(ops_dst, ignore_errors=True)
    shutil.copytree(ops_src, ops_dst, ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))

    print("  Extension installed successfully.")
    print()

    # Step 2: Install tinta-designer Skill (optional, with prompt)
    print("[2/2] Checking AI agent skills directories...")
    skill_assets = get_asset_dir("skills/tinta-designer")
    if skill_assets and skill_assets.exists():
        detected_dirs = find_agent_skills_dirs()
        if detected_dirs:
            for skill_dir in detected_dirs:
                prompt_text = (
                    f"Found agent skills directory at: {skill_dir}\n"
                    "Install the 'tinta-designer' skill here? [Y/n]: "
                )
                install_choice = "y"
                if not auto_yes:
                    try:
                        reply = input(prompt_text).strip().lower()
                        if reply and reply not in ("y", "yes", "s", "sim"):
                            install_choice = "n"
                    except (EOFError, KeyboardInterrupt):
                        install_choice = "n"

                if install_choice in ("y", "yes", "s", "sim"):
                    target_skill = skill_dir / "tinta-designer"
                    if target_skill.exists():
                        shutil.rmtree(target_skill, ignore_errors=True)
                    shutil.copytree(skill_assets, target_skill)
                    print(f"  Installed tinta-designer skill to {target_skill}")
                else:
                    print("  Skipped skill installation.")
        else:
            print("  No global AI agent skill directories detected. Skipping skill copy.")
    else:
        print("  Skill assets not bundled. Skipping.")

    print()
    print("=" * 60)
    print(" Setup Complete!")
    print("=" * 60)
    print()
    print("Add this entry to your MCP client configuration (Claude Desktop, Cursor, etc.):")
    print()
    print("""{
  "mcpServers": {
    "tinta": {
      "command": "tinta"
    }
  }
}""")
    print()
    print("If running on demand with uvx, use:")
    print("""{
  "mcpServers": {
    "tinta": {
      "command": "uvx",
      "args": ["tinta-mcp"]
    }
  }
}""")
    print()
