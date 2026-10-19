"""Resolution of the gdbus executable and waiting for extension response files.

On Windows, gdbus is not on a stock PATH, and a bare "gdbus" resolved via PATH could
belong to an unrelated GLib install (MSYS2, GIMP, ...) that autolaunches its own empty
session bus instead of finding Inkscape's. The only copy that reliably shares Inkscape's
D-Bus session is the one bundled next to inkscape.exe itself, so it must be resolved as
a sibling of the actual Inkscape executable rather than looked up by bare name.

New in the Windows fork of Shriinivas/inkmcp, 2026-07. AGPL-3.0, same as upstream.
"""

import getpass
import os
import platform
import shutil
import time
from collections.abc import Generator
from contextlib import contextmanager
from pathlib import Path
from typing import Any, cast


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
        getuid = getattr(os, "getuid", None)
        if not callable(getuid):
            raise OSError("Cannot determine the current user ID for the IPC directory")
        uid = getuid()
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


def response_path(ipc_dir: Path, request_id: str) -> Path:
    """Build the only response path accepted by the protocol."""
    if len(request_id) != 32 or any(char not in "0123456789abcdef" for char in request_id):
        raise ValueError("Invalid IPC request ID")
    return ipc_dir / f"mcp_response_{request_id}.json"


@contextmanager
def ipc_process_lock(ipc_dir: Path) -> Generator[None, None, None]:
    """Claim the single shared Inkscape request slot across server processes."""
    path = ipc_dir / "server.lock"
    with path.open("a+b") as lock_file:
        if os.name == "nt":
            import msvcrt

            lock_file.seek(0)
            if path.stat().st_size == 0:
                lock_file.write(b"0")
                lock_file.flush()
            lock_file.seek(0)
            try:
                msvcrt.locking(lock_file.fileno(), msvcrt.LK_NBLCK, 1)
            except OSError as error:
                raise RuntimeError("Another Tinta server is using the Inkscape session") from error
            try:
                yield
            finally:
                lock_file.seek(0)
                msvcrt.locking(lock_file.fileno(), msvcrt.LK_UNLCK, 1)
        else:
            import fcntl

            try:
                fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            except OSError as error:
                raise RuntimeError("Another Tinta server is using the Inkscape session") from error
            try:
                yield
            finally:
                fcntl.flock(lock_file.fileno(), fcntl.LOCK_UN)


def find_inkscape_exe() -> str | None:
    """Locate the Inkscape executable via env override, PATH, registry, or common install dirs."""
    env_override = os.environ.get("INKSCAPE_COMMAND")
    if env_override and os.path.isfile(env_override):
        return env_override

    which_result = shutil.which("inkscape") or shutil.which("inkscape.exe")
    if which_result:
        return which_result

    if platform.system() == "Windows":
        for candidate in _windows_registry_inkscape_paths():
            if candidate and os.path.isfile(candidate):
                return candidate
        for candidate in (
            r"C:\Program Files\Inkscape\bin\inkscape.exe",
            r"C:\Program Files (x86)\Inkscape\bin\inkscape.exe",
        ):
            if os.path.isfile(candidate):
                return candidate

    return None


def _windows_registry_inkscape_paths():
    import winreg

    # Pyright's Linux target stubs do not expose the Windows-only winreg API.
    registry = cast(Any, winreg)
    for hive in (registry.HKEY_LOCAL_MACHINE, registry.HKEY_CURRENT_USER):
        try:
            key_path = r"SOFTWARE\Microsoft\Windows\CurrentVersion\App Paths\inkscape.exe"
            with registry.OpenKey(hive, key_path) as key:
                value, _ = registry.QueryValueEx(key, "")
                yield value
        except OSError:
            continue


def find_gdbus() -> str:
    """Return the path to invoke for gdbus calls against Inkscape's D-Bus session."""
    env_override = os.environ.get("TINTA_GDBUS_PATH")
    if env_override and os.path.isfile(env_override):
        return env_override

    if platform.system() != "Windows":
        return "gdbus"

    inkscape_exe = find_inkscape_exe()
    if inkscape_exe:
        sibling = Path(inkscape_exe).parent / "gdbus.exe"
        if sibling.is_file():
            return str(sibling)

    raise FileNotFoundError(
        "Could not locate gdbus.exe. On Windows this must be the copy bundled with "
        "Inkscape - install Inkscape or set TINTA_GDBUS_PATH to the full path of "
        "gdbus.exe (e.g. C:\\Program Files\\Inkscape\\bin\\gdbus.exe)."
    )


def wait_for_response_file(
    response_file: str, timeout: float = 5.0, interval: float = 0.05
) -> bool:
    """Poll for the extension's response file to appear, rather than assuming it is
    already present the instant the gdbus call returns. Returns True once found."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if os.path.exists(response_file):
            return True
        time.sleep(interval)
    return os.path.exists(response_file)
