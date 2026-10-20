import os

import pytest

from tintamcp.agents.configuration import ConfigChange
from tintamcp.agents.writes import apply_config_change


def change(path, before: bytes | None = b"original", after=b"new"):
    return ConfigChange(path, before, after, "create" if before is None else "update", "preview")


def test_update_backups_are_unique_and_contain_original_bytes(tmp_path):
    path = tmp_path / "config"
    path.write_bytes(b"original")
    first = apply_config_change(change(path))
    assert first.backup_path is not None
    assert path.read_bytes() == b"new"
    assert first.backup_path.read_bytes() == b"original"
    second = apply_config_change(change(path, b"new", b"newer"))
    assert second.backup_path is not None
    assert second.backup_path != first.backup_path
    assert first.backup_path.read_bytes() == b"original"
    assert second.backup_path.read_bytes() == b"new"


def test_create_missing_directory_without_backup(tmp_path):
    path = tmp_path / "nested/config"
    result = apply_config_change(change(path, None))
    assert path.read_bytes() == b"new"
    assert result.backup_path is None


@pytest.mark.parametrize("current", [b"edited", None])
def test_changed_or_deleted_snapshot_is_not_overwritten(tmp_path, current):
    path = tmp_path / "config"
    if current is not None:
        path.write_bytes(current)
    with pytest.raises(ValueError, match="changed"):
        apply_config_change(change(path))
    assert (path.read_bytes() if path.exists() else None) == current
    assert list(tmp_path.iterdir()) == ([path] if current is not None else [])


def test_file_created_after_preview_is_not_overwritten(tmp_path):
    path = tmp_path / "config"
    path.write_bytes(b"someone else")
    with pytest.raises(ValueError, match="changed"):
        apply_config_change(change(path, None))
    assert path.read_bytes() == b"someone else"


def test_replace_failure_keeps_original_and_cleans_temporary_file(tmp_path, monkeypatch):
    path = tmp_path / "config"
    path.write_bytes(b"original")

    def fail(*args):
        raise PermissionError("denied")

    monkeypatch.setattr(os, "replace", fail)
    with pytest.raises(PermissionError):
        apply_config_change(change(path))
    assert path.read_bytes() == b"original"
    assert len(list(tmp_path.glob("*.bak-*"))) == 1
    assert not list(tmp_path.glob("*.tmp-*"))


def test_backup_failure_keeps_original(tmp_path, monkeypatch):
    from tintamcp.agents import writes

    path = tmp_path / "config"
    path.write_bytes(b"original")

    def fail(*args):
        raise PermissionError("denied")

    monkeypatch.setattr(writes, "copy_private", fail)
    with pytest.raises(PermissionError):
        apply_config_change(change(path))
    assert path.read_bytes() == b"original"


def test_unchanged_change_is_rejected_without_writing(tmp_path):
    path = tmp_path / "missing"
    with pytest.raises(ValueError):
        apply_config_change(ConfigChange(path, None, None, "unchanged", ""))
    assert not path.exists()


@pytest.mark.skipif(os.name == "nt", reason="POSIX permission bits")
def test_posix_backup_and_replacement_permissions_match_original(tmp_path):
    path = tmp_path / "config"
    path.write_bytes(b"original")
    path.chmod(0o600)
    result = apply_config_change(change(path))
    assert result.backup_path is not None
    assert path.stat().st_mode & 0o777 == 0o600
    assert result.backup_path.stat().st_mode & 0o777 == 0o600


def test_links_are_rejected_even_if_created_after_preview(tmp_path):
    real = tmp_path / "real"
    real.write_bytes(b"original")
    link = tmp_path / "link"
    try:
        link.symlink_to(real)
    except OSError:
        pytest.skip("Creating symlinks is not permitted")
    with pytest.raises(ValueError, match="redirect"):
        apply_config_change(change(link))
    assert real.read_bytes() == b"original"


def test_edit_after_backup_is_detected_and_backup_location_reported(tmp_path, monkeypatch):
    from tintamcp.agents import writes

    path = tmp_path / "config"
    path.write_bytes(b"original")
    copy = writes.copy_private

    def edit_after_backup(source, destination):
        copy(source, destination)
        if ".bak-" in destination.name:
            path.write_bytes(b"new user edit")

    monkeypatch.setattr(writes, "copy_private", edit_after_backup)
    with pytest.raises(ValueError, match="changed") as exc:
        apply_config_change(change(path))
    assert path.read_bytes() == b"new user edit"
    assert len(list(tmp_path.glob("*.bak-*"))) == 1
    assert any("backup" in note for note in getattr(exc.value, "__notes__", ()))
    assert not list(tmp_path.glob("*.tmp-*"))


@pytest.mark.skipif(os.name != "nt", reason="Windows security descriptors")
def test_windows_backup_and_replacement_preserve_protected_dacl(tmp_path):
    import ctypes
    from ctypes import wintypes

    security = ctypes.WinDLL("advapi32", use_last_error=True)
    get_security = security.GetFileSecurityW
    get_security.argtypes = [
        wintypes.LPCWSTR,
        wintypes.DWORD,
        ctypes.c_void_p,
        wintypes.DWORD,
        ctypes.POINTER(wintypes.DWORD),
    ]
    get_security.restype = wintypes.BOOL
    set_security = security.SetFileSecurityW
    set_security.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, ctypes.c_void_p]
    set_security.restype = wintypes.BOOL

    def descriptor(path):
        needed = wintypes.DWORD()
        get_security(str(path), 4, None, 0, ctypes.byref(needed))
        buffer = ctypes.create_string_buffer(needed.value)
        assert get_security(str(path), 4, buffer, needed, ctypes.byref(needed))
        return buffer

    path = tmp_path / "config"
    path.write_bytes(b"original")
    # Protect this fixture's current DACL from inheritance. It still grants
    # access to the test user; no real user configuration is involved.
    initial = descriptor(path)
    assert set_security(str(path), 0x80000004, initial)
    protected = descriptor(path).raw
    applied = apply_config_change(change(path))
    assert applied.backup_path is not None
    assert descriptor(applied.backup_path).raw == protected
    assert descriptor(path).raw == protected
