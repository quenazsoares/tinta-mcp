"""Backup and replace a single approved configuration snapshot."""

import ctypes
import os
import shutil
import tempfile
from dataclasses import dataclass
from pathlib import Path

from .configuration import ConfigChange
from .detection import redirected


@dataclass(frozen=True)
class AppliedChange:
    path: Path
    backup_path: Path | None


def copy_private(source: Path, destination: Path) -> None:
    """Copy an existing configuration with its access protections.

    On supported Windows versions CopyFileW copies security resource properties
    (including the DACL); chmod/copy2 alone do not preserve Windows ACLs.
    The caller reserves a unique destination before calling this function.
    """
    if os.name == "nt":
        kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        copy = kernel.CopyFileW
        copy.argtypes = [ctypes.c_wchar_p, ctypes.c_wchar_p, ctypes.c_int]
        copy.restype = ctypes.c_int
        if not copy(str(source), str(destination), False):
            raise ctypes.WinError(ctypes.get_last_error())
    else:
        shutil.copy2(source, destination)


def _snapshot(path: Path) -> bytes | None:
    try:
        return path.read_bytes()
    except FileNotFoundError:
        return None


def apply_config_change(change: ConfigChange) -> AppliedChange:
    if change.status not in ("create", "update") or change.after is None:
        raise ValueError("Only approved create/update changes can be applied")
    path = change.path
    if redirected(path):
        raise ValueError(
            "Configuration path is redirected; select its resolved destination explicitly"
        )
    if _snapshot(path) != change.before:
        raise ValueError("Configuration changed since preview; prepare a new preview")
    path.parent.mkdir(parents=True, exist_ok=True)
    backup = None
    temporary = None
    try:
        if change.before is not None:
            fd, name = tempfile.mkstemp(prefix=path.name + ".bak-", dir=path.parent)
            os.close(fd)
            backup = Path(name)
            try:
                copy_private(path, backup)
            except BaseException:
                backup.unlink(missing_ok=True)
                raise
            if backup.read_bytes() != change.before:
                raise ValueError("Configuration changed while creating the backup")
        fd, name = tempfile.mkstemp(prefix=path.name + ".tmp-", dir=path.parent)
        os.close(fd)
        temporary = Path(name)
        if change.before is not None:
            copy_private(path, temporary)
        with temporary.open("wb") as stream:
            stream.write(change.after)
            stream.flush()
            os.fsync(stream.fileno())
        if redirected(path) or _snapshot(path) != change.before:
            raise ValueError("Configuration changed since preview; prepare a new preview")
        os.replace(temporary, path)
        return AppliedChange(path, backup)
    except (OSError, ValueError) as error:
        # Keep the backup available after a failed replacement, and make its
        # location available to the coordinator without printing file contents.
        if backup is not None and backup.exists():
            error.add_note(f"Original configuration backup: {backup}")
        raise
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)
