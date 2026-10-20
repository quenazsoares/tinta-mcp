"""
Installer module for Tinta MCP.

Installs the Inkscape extension and optionally the tinta-designer skill
directly from the installed Python package into the user's environment.
"""

from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

from tintamcp.agents.catalog import AgentContext, get_agent_profiles
from tintamcp.agents.configuration import McpServerSpec, prepare_mcp_change
from tintamcp.agents.detection import DetectedTarget, detect_agent_targets, redirected
from tintamcp.agents.writes import apply_config_change


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
                    return ext_dir
        except Exception:
            pass

    # 2. Fallback to standard platform locations
    if sys.platform == "win32":
        appdata = os.environ.get("APPDATA")
        if appdata:
            ext_dir = Path(appdata) / "inkscape" / "extensions"
            return ext_dir
    else:
        ext_dir = Path.home() / ".config" / "inkscape" / "extensions"
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


def get_agent_context() -> AgentContext:
    """Capture user paths once; tests supply a temporary context instead."""
    return AgentContext(Path.home(), sys.platform, dict(os.environ))


def _confirm(prompt: str, *, default: bool = True) -> bool:
    try:
        answer = input(prompt).strip().lower()
        return default if not answer else answer in ("y", "yes", "s", "sim")
    except (EOFError, KeyboardInterrupt):
        return False


def _skill_matches(source: Path, destination: Path) -> bool:
    if not destination.is_dir() or redirected(destination):
        return False
    files = [
        p
        for p in source.rglob("*")
        if p.is_file() and "__pycache__" not in p.parts and p.suffix != ".pyc"
    ]
    return all(
        (destination / p.relative_to(source)).is_file()
        and not redirected(destination / p.relative_to(source))
        and p.read_bytes() == (destination / p.relative_to(source)).read_bytes()
        for p in files
    )


def _replace_skill(source: Path, destination: Path) -> None:
    """Prepare a complete copy, retaining user additions, before replacing it."""
    destination.parent.mkdir(parents=True, exist_ok=True)
    work = Path(tempfile.mkdtemp(prefix=".tinta-skill-", dir=destination.parent))
    staged, previous = work / "new", work / "previous"
    try:
        if destination.exists():
            shutil.copytree(destination, staged, symlinks=True)
        else:
            staged.mkdir()
        # Preserve extra symlinks as links, but never write through one when
        # overlaying a bundled file or directory into the prepared copy.
        if any(redirected(staged / p.relative_to(source)) for p in source.rglob("*")):
            raise ValueError("A bundled skill path is redirected; update the skill manually.")
        shutil.copytree(
            source,
            staged,
            dirs_exist_ok=True,
            ignore=shutil.ignore_patterns("__pycache__", "*.pyc"),
        )
        if destination.exists():
            destination.rename(previous)
        try:
            staged.rename(destination)
        except BaseException as error:
            if previous.exists():
                try:
                    previous.rename(destination)
                except OSError:
                    error.add_note(f"Previous skill retained at {previous}; restore it manually.")
            raise
        if previous.exists():
            shutil.rmtree(previous)
    finally:
        # If restoration failed, leave the previous installation available.
        if not previous.exists():
            shutil.rmtree(work, ignore_errors=True)


def _install_skill(target: DetectedTarget, source: Path, auto_yes: bool, dry_run: bool) -> None:
    destination = target.path / "tinta-designer"
    print(f"  Skill destination: {destination}")
    if target.requires_explicit_selection or redirected(destination):
        print(
            "  Skipped: redirected destination. Select the profile explicitly with --agent, "
            "or install manually if the skill directory itself is a link."
        )
        return
    if _skill_matches(source, destination):
        print("  The tinta-designer skill is already up to date.")
        return
    duplicate = bool(target.other_skill_paths) and not destination.exists()
    if duplicate:
        for path in target.other_skill_paths:
            print(f"  An existing skill is installed at: {path / 'tinta-designer'}")
        print(
            "  Installing here creates another copy for clients that use this path. "
            "Existing copies will be kept."
        )
    if dry_run:
        print("  Would install/update the tinta-designer skill.")
        return
    if duplicate:
        if not sys.stdin.isatty() or not _confirm(
            "  Create another skill copy here? [y/N]: ", default=False
        ):
            print("  Skipped creating another skill copy.")
            return
    elif not auto_yes and not _confirm("  Install/update the 'tinta-designer' skill here? [Y/n]: "):
        print("  Skipped skill installation.")
        return
    _replace_skill(source, destination)
    print(f"  Installed tinta-designer skill to {destination}")


def _configure_mcp(target: DetectedTarget, dry_run: bool) -> None:
    print(f"  MCP configuration: {target.path}")
    if target.requires_explicit_selection:
        print("  Skipped: redirected destination. Select the profile explicitly with --agent.")
        return
    uvx = shutil.which("uvx")
    if not uvx:
        raise ValueError(
            "uvx was not found on PATH. Install uv, then configure MCP manually or retry."
        )
    before = target.path.read_bytes() if target.path.exists() else None
    change = prepare_mcp_change(
        target, before, McpServerSpec(str(Path(uvx).resolve()), ("tinta-mcp",))
    )
    print(change.preview)
    if change.status == "conflict":
        raise ValueError("Configuration conflict; the file was not modified.")
    if change.status == "unchanged" or dry_run:
        return
    if not sys.stdin.isatty():
        print("  Skipped MCP configuration: approval requires an interactive terminal.")
        return
    if not _confirm("  Approve this MCP configuration change? [y/N]: ", default=False):
        print("  Skipped MCP configuration by user choice.")
        return
    applied = apply_config_change(change)
    print(f"  Configured Tinta in {applied.path}")
    if applied.backup_path:
        print(f"  Backup: {applied.backup_path}")
        print("  To restore, close the client and copy the backup over the configuration file.")
    if target.adapter == "toml-codex":
        print("  Restart the Codex client to load the updated MCP configuration.")
    else:
        print("  Reload the client's MCP servers, or restart the client.")


def _install_extension(ext_dir: Path, assets: Path) -> None:
    ext_dir.mkdir(parents=True, exist_ok=True)
    for name in ("inkscape_mcp.py", "inkscape_mcp.inx"):
        (ext_dir / name).unlink(missing_ok=True)
    legacy = ext_dir / "inkmcp"
    if legacy.is_dir():
        shutil.rmtree(legacy)
    for name in ("tinta_mcp.py", "tinta_mcp.inx"):
        shutil.copy2(assets / name, ext_dir / name)
    ops = ext_dir / "tinta_mcp_ops"
    if ops.exists():
        shutil.rmtree(ops)
    shutil.copytree(
        assets / "tinta_mcp_ops", ops, ignore=shutil.ignore_patterns("__pycache__", "*.pyc")
    )


def run_installer(
    auto_yes: bool = False,
    *,
    agents: tuple[str, ...] = (),
    configure_mcp: bool = False,
    dry_run: bool = False,
) -> None:
    """Install extension/skills and optionally request per-file MCP approval."""
    targets = detect_agent_targets(get_agent_context(), agents)
    print("=" * 60)
    print(" Tinta MCP — Installation" + (" (dry run)" if dry_run else ""))
    print("=" * 60)
    errors = []
    print("\n[1/2] Locating Inkscape extensions directory...")
    ext_dir = get_inkscape_extensions_dir()
    ext_assets = get_asset_dir("extension")
    if ext_dir is None or ext_assets is None:
        print("Error: Could not locate Inkscape extensions directory or bundled extension assets.")
        raise SystemExit(1)
    print(f"  Target: {ext_dir}")
    try:
        if dry_run:
            print("  Would install/update the Inkscape extension.")
        else:
            _install_extension(ext_dir, ext_assets)
            print("  Extension installed successfully. Restart Inkscape to load it.")
    except OSError as error:
        errors.append("Inkscape extension")
        print(f"  Error installing extension: {error}")
    print("\n[2/2] Looking for supported client configurations...")
    skill_assets = get_asset_dir("skills/tinta-designer")
    previous_labels: tuple[str, ...] = ()
    for target in targets:
        if target.labels != previous_labels:
            print(f"\n  {', '.join(target.labels)} ({target.evidence})")
            previous_labels = target.labels
        try:
            if target.capability == "skill":
                if skill_assets is None:
                    raise ValueError("Designer skill assets are missing from the package")
                _install_skill(target, skill_assets, auto_yes, dry_run)
            elif configure_mcp:
                _configure_mcp(target, dry_run)
            else:
                print(f"  MCP configuration available: {target.path}")
        except (OSError, ValueError) as error:
            errors.append(str(target.path))
            print(f"  Error: {error}")
            for note in getattr(error, "__notes__", ()):
                print(f"  {note}")
    if not targets:
        print("  No known agent configuration directories found. Use --agent to choose a profile.")
    available_ids = {i for t in targets for i in t.profile_ids}
    for profile in get_agent_profiles():
        if profile.id in available_ids and profile.mcp is None and configure_mcp:
            print(f"  {profile.label}: MCP automatic setup is not supported; configure manually.")
    if not configure_mcp:
        print("\nTo preview MCP configuration: tinta-mcp install --configure-mcp --dry-run")
        print("To configure MCP with approval: tinta-mcp install --configure-mcp")
        print('Manual MCP entry: command="uvx", args=["tinta-mcp"] (use your client format).')
    print("\nInstallation finished" + (f" with {len(errors)} error(s)." if errors else "."))
    if errors:
        raise SystemExit(1)


def install_from_args(arguments: list[str]) -> None:
    parser = argparse.ArgumentParser(
        prog="tinta-mcp install",
        description="Install the Tinta extension and optional agent integrations.",
    )
    parser.add_argument(
        "--yes",
        "-y",
        action="store_true",
        help="Accept skill installation/updates; extra copies and MCP changes require approval",
    )
    parser.add_argument(
        "--agent",
        action="append",
        default=[],
        choices=[p.id for p in get_agent_profiles()],
        help="Restrict agent integrations (repeatable)",
    )
    parser.add_argument(
        "--configure-mcp",
        action="store_true",
        help="Preview and request approval for each MCP configuration",
    )
    parser.add_argument(
        "--dry-run", action="store_true", help="Preview all installation steps without writing"
    )
    options = parser.parse_args(arguments)
    run_installer(
        auto_yes=options.yes,
        agents=tuple(options.agent),
        configure_mcp=options.configure_mcp,
        dry_run=options.dry_run,
    )
