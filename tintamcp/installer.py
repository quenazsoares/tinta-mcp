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
from importlib.metadata import PackageNotFoundError, version
from pathlib import Path
from typing import Literal

from tintamcp.agents.catalog import AgentContext, get_agent_profiles
from tintamcp.agents.configuration import McpServerSpec, prepare_mcp_change
from tintamcp.agents.detection import DetectedTarget, detect_agent_targets, redirected
from tintamcp.agents.writes import apply_config_change


def get_inkscape_extensions_dir() -> Path | None:
    """Locate the Inkscape user extensions directory."""
    candidates = []
    custom = os.environ.get("INKSCAPE_COMMAND")
    if custom:
        resolved = shutil.which(custom) or custom
        custom_path = Path(resolved)
        if sys.platform == "win32" and custom_path.suffix.lower() == ".exe":
            candidates.append(custom_path.with_suffix(".com"))
        candidates.append(custom_path)
    if sys.platform == "win32":
        candidates.extend(
            [
                Path(r"C:\Program Files\Inkscape\bin\inkscape.com"),
                Path(r"C:\Program Files\Inkscape\inkscape.com"),
                Path(r"C:\Program Files (x86)\Inkscape\bin\inkscape.com"),
            ]
        )
    elif binary := shutil.which("inkscape"):
        candidates.append(Path(binary))
    for candidate in dict.fromkeys(candidates):
        if not candidate.is_file():
            continue
        try:
            result = subprocess.run(
                [str(candidate), "--user-data-directory"], capture_output=True, text=True, timeout=5
            )
            lines = [line.strip() for line in result.stdout.splitlines() if line.strip()]
            if result.returncode == 0 and lines and Path(lines[-1]).is_absolute():
                return Path(lines[-1]) / "extensions"
        except (OSError, subprocess.TimeoutExpired, UnicodeError):
            pass
        print(f"  Warning: could not query {candidate}; trying another executable.")
    if sys.platform == "win32":
        appdata = os.environ.get("APPDATA")
        fallback = Path(appdata) / "inkscape/extensions" if appdata else None
    else:
        fallback = Path.home() / ".config/inkscape/extensions"
    if fallback:
        print(
            f"  Warning: using fallback directory {fallback}. "
            "Check it against your Inkscape preferences."
        )
    return fallback


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


def _install_skill(target: DetectedTarget, source: Path, auto_yes: bool, dry_run: bool) -> str:
    destination = target.path / "tinta-designer"
    print(f"  Skill destination: {destination}")
    if target.requires_explicit_selection or redirected(destination):
        print(
            "  Skipped: redirected destination. Select the profile explicitly with --agent, "
            "or install manually if the skill directory itself is a link."
        )
        return "skipped"
    if _skill_matches(source, destination):
        print("  The tinta-designer skill is already up to date.")
        return "unchanged"
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
        return "planned"
    if duplicate:
        if not sys.stdin.isatty() or not _confirm(
            "  Create another skill copy here? [y/N]: ", default=False
        ):
            print("  Skipped creating another skill copy.")
            return "skipped"
    elif not auto_yes and not _confirm("  Install/update the 'tinta-designer' skill here? [Y/n]: "):
        print("  Skipped skill installation.")
        return "skipped"
    _replace_skill(source, destination)
    print(f"  Installed tinta-designer skill to {destination}")

    return "installed"


def _configure_mcp(target: DetectedTarget, dry_run: bool) -> str:
    print(f"  MCP configuration: {target.path}")
    if target.requires_explicit_selection:
        print("  Skipped: redirected destination. Select the profile explicitly with --agent.")
        return "skipped"
    uvx = shutil.which("uvx")
    if not uvx:
        raise ValueError(
            "uvx was not found on PATH. Install uv, then configure MCP manually or retry."
        )
    before = target.path.read_bytes() if target.path.exists() else None
    change = prepare_mcp_change(
        target, before, McpServerSpec(str(Path(uvx).resolve()), ("tinta-mcp",))
    )
    if change.status == "conflict":
        raise ValueError(change.preview)
    print(change.preview)
    if change.status == "unchanged":
        return "unchanged"
    if dry_run:
        return "planned"
    if not sys.stdin.isatty():
        print("  Skipped MCP configuration: approval requires an interactive terminal.")
        return "skipped"
    if not _confirm("  Approve this MCP configuration change? [y/N]: ", default=False):
        print("  Skipped MCP configuration by user choice.")
        return "skipped"
    applied = apply_config_change(change)
    print(f"  Configured Tinta in {applied.path}")
    if applied.backup_path:
        print(f"  Backup: {applied.backup_path}")
        print("  To restore, close the client and copy the backup over the configuration file.")
    if target.adapter == "toml-codex":
        print("  Restart the Codex client to load the updated MCP configuration.")
    else:
        print("  Reload the client's MCP servers, or restart the client.")

    return "configured"


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
    dry_run: bool = False,
    only: Literal["extension", "skills", "mcp"] | None = None,
) -> None:
    """Install selected components; MCP writes always need explicit approval."""
    if only not in (None, "extension", "skills", "mcp"):
        raise ValueError("Unknown installation component")
    include_mcp = only in (None, "mcp")
    try:
        package_version = version("tinta-mcp")
    except PackageNotFoundError:
        package_version = "unknown (package metadata unavailable)"
    print("=" * 60)
    print(f" Tinta MCP {package_version} — Installation" + (" (dry run)" if dry_run else ""))
    print("=" * 60)
    results: list[tuple[str, str]] = []

    def detection_error(label: str, message: str) -> None:
        print(f"  Error detecting {label}: {message}")
        results.append((label, "error"))

    targets = (
        detect_agent_targets(
            get_agent_context(),
            agents,
            capability="skill" if only == "skills" else "mcp" if only == "mcp" else None,
            on_error=detection_error,
        )
        if only != "extension"
        else ()
    )
    if only in (None, "extension"):
        print("\nInkscape extension")
        try:
            ext_dir = get_inkscape_extensions_dir()
            assets = get_asset_dir("extension")
            if ext_dir is None or assets is None:
                raise ValueError(
                    "Could not locate the Inkscape profile or bundled extension assets"
                )
            print(f"  Target: {ext_dir}")
            if dry_run:
                print("  Would install/update the Inkscape extension.")
            else:
                _install_extension(ext_dir, assets)
                print("  Extension installed successfully. Restart Inkscape to load it.")
            results.append(("Inkscape extension", "planned" if dry_run else "installed"))
        except (OSError, ValueError) as error:
            print(f"  Error: {error}")
            results.append(("Inkscape extension", "error"))
    skill_assets = get_asset_dir("skills/tinta-designer") if only != "mcp" else None
    previous_labels: tuple[str, ...] = ()
    for target in targets:
        if target.labels != previous_labels:
            print(f"\n  {', '.join(target.labels)} ({target.evidence})")
            previous_labels = target.labels
        label = f"{', '.join(target.labels)} {'skill' if target.capability == 'skill' else 'MCP'}"
        try:
            if target.capability == "skill":
                if skill_assets is None:
                    raise ValueError("Designer skill assets are missing from the package")
                status = _install_skill(target, skill_assets, auto_yes, dry_run)
            else:
                status = _configure_mcp(target, dry_run)
            results.append((label, status))
        except (OSError, ValueError) as error:
            print(f"  Error: {error}")
            for note in getattr(error, "__notes__", ()):
                print(f"  {note}")
            results.append((label, "error"))
    if not targets and only != "extension" and not (only == "mcp" and agents):
        print("  No usable client destinations found. Use --agent to choose a profile.")
    available_ids = {i for t in targets for i in t.profile_ids} | set(agents)
    for profile in get_agent_profiles():
        if profile.id in available_ids and profile.mcp is None and include_mcp:
            print(f"  {profile.label}: MCP automatic setup is not supported; configure manually.")
            results.append((f"{profile.label} MCP", "skipped (manual setup)"))
    if only == "skills":
        print("\nTo configure only MCP: tinta-mcp install --only mcp")
        print('Manual MCP entry: command="uvx", args=["tinta-mcp"] (use your client format).')
    print("\nSummary" + (" (dry run; no changes written)" if dry_run else ""))
    for label, status in results:
        print(f"  {label}: {status}")
    errors = sum(status == "error" for _, status in results)
    print("\nInstallation finished" + (f" with {errors} error(s)." if errors else "."))
    if errors:
        raise SystemExit(1)


def add_install_arguments(parser: argparse.ArgumentParser) -> None:
    """Register installer options on the CLI's install subcommand."""
    parser.add_argument(
        "--yes",
        "-y",
        action="store_true",
        help="Install or update skills without prompting; extra copies and MCP changes require approval",
    )
    parser.add_argument(
        "--agent",
        action="append",
        default=[],
        choices=[p.id for p in get_agent_profiles()],
        help="Choose a client; repeat to select more than one",
    )
    parser.add_argument(
        "--dry-run", action="store_true", help="Show planned changes without modifying files"
    )
    parser.add_argument(
        "--only",
        choices=("extension", "skills", "mcp"),
        help="Run only this component; MCP changes still require approval",
    )
