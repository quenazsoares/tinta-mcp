"""Opt-in live tests own their Inkscape process, profile, IPC and document."""

import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
import uuid
import xml.etree.ElementTree as ET
from pathlib import Path

import pytest


def pytest_addoption(parser):
    parser.addoption(
        "--live-inkscape",
        action="store_true",
        help="Run live tests in disposable Inkscape instances",
    )


def pytest_configure(config):
    config.addinivalue_line(
        "markers", "live: requires an explicitly requested disposable Inkscape instance"
    )


def pytest_collection_modifyitems(config, items):
    if not config.getoption("--live-inkscape"):
        for item in items:
            if "live" in item.keywords:
                item.add_marker(
                    pytest.mark.skip(reason="Use --live-inkscape for disposable live integration")
                )


class LiveDocument:
    def __init__(self, connection, folder):
        self.connection = connection
        self.folder = folder

    def snapshot(self):
        path = self.folder / f"snapshot-{uuid.uuid4().hex}.svg"
        for action, parameter in (
            ("export-type", '[<"svg">]'),
            ("export-filename", "[<" + json.dumps(str(path).replace("\\", "/")) + ">]"),
            ("export-overwrite", "[<true>]"),
            ("export-area-page", "[<true>]"),
            ("export-do", "[]"),
        ):
            assert self.connection.activate_action(action, parameter), action
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            try:
                return ET.parse(path).getroot()
            except (OSError, ET.ParseError):
                time.sleep(0.05)
        pytest.fail("Native SVG snapshot was not completed")


@pytest.fixture
def live_document(request, tmp_path, monkeypatch):
    if not request.config.getoption("--live-inkscape"):
        pytest.skip("Live integration requires --live-inkscape")
    from tintamcp import server
    from tintamcp.gdbus_util import find_gdbus, find_inkscape_exe, get_tinta_ipc_dir

    executable = os.environ.get("TINTA_TEST_INKSCAPE") or find_inkscape_exe()
    if not executable or not Path(executable).is_file():
        pytest.fail("Live tests requested but Inkscape executable is unavailable")
    executable_path = Path(executable)
    if sys.platform == "win32" and executable_path.with_suffix(".exe").is_file():
        executable_path = executable_path.with_suffix(".exe")
    profile, temp = tmp_path / "profile", tmp_path / "temp"
    extensions = profile / "extensions"
    extensions.mkdir(parents=True)
    temp.mkdir()
    if sys.platform != "win32":
        temp.chmod(0o700)
    source = Path(__file__).resolve().parents[1] / "extension"
    for filename in ("tinta_mcp.py", "tinta_mcp.inx"):
        shutil.copy2(source / filename, extensions / filename)
    shutil.copytree(
        source / "tinta_mcp_ops",
        extensions / "tinta_mcp_ops",
        ignore=shutil.ignore_patterns("__pycache__", "*.pyc"),
    )
    (profile / "preferences.xml").write_text(
        '<inkscape><group id="boot" mode="1"><group id="shown" ver1.2.2="1" ver1.4="1" ver1.4.4="1"/></group></inkscape>',
        encoding="utf-8",
    )
    document = tmp_path / "discardable.svg"
    document.write_text(
        '<svg xmlns="http://www.w3.org/2000/svg" width="320" height="200" viewBox="0 0 320 200"><rect id="fixture_anchor" x="10" y="10" width="20" height="20"/></svg>',
        encoding="utf-8",
    )
    namespace = "TintaTest" + uuid.uuid4().hex
    for name in ("LOGNAME", "USER", "LNAME", "USERNAME"):
        monkeypatch.setenv(name, namespace)
    for name in ("TEMP", "TMP", "TMPDIR"):
        monkeypatch.setenv(name, str(temp))
    monkeypatch.setenv("LOCALAPPDATA", str(temp / "localappdata"))
    monkeypatch.setenv("XDG_RUNTIME_DIR", str(temp))
    monkeypatch.setenv("INKSCAPE_PROFILE_DIR", str(profile))
    monkeypatch.setenv("INKSCAPE_COMMAND", str(executable_path))
    monkeypatch.setenv("PYTHONDONTWRITEBYTECODE", "1")
    monkeypatch.setattr(tempfile, "tempdir", str(temp))
    try:
        gdbus = find_gdbus()
    except OSError as error:
        pytest.fail(f"Live tests requested but gdbus is unavailable: {error}")
    monkeypatch.setenv("TINTA_GDBUS_PATH", gdbus)
    connection = server.InkscapeConnection()
    connection.dbus_service = "org.inkscape.Inkscape." + namespace
    connection.dbus_path = "/" + connection.dbus_service.replace(".", "/")
    monkeypatch.setattr(server, "_inkscape_connection", connection)
    # A preview failure must fail the test, not spawn an alternate renderer.
    dispatch = server.dispatch_operation

    def native_only(payload):
        if payload.get("operation") == "export-document-image":
            pytest.fail("Native preview failed; secondary CLI renderer is forbidden in live tests")
        return dispatch(payload)

    monkeypatch.setattr(server, "dispatch_operation", native_only)
    ipc = get_tinta_ipc_dir()
    expected_parent = (
        (temp / "localappdata" / "Temp" / "tintamcp") if sys.platform == "win32" else temp
    )
    assert ipc.resolve().parent == expected_parent.resolve() and not ipc.is_symlink()
    if sys.platform == "win32":
        assert ipc.name == namespace
    else:
        assert ipc.name == "tintamcp" and not (ipc.stat().st_mode & 0o077)
    startup = None
    if sys.platform == "win32":
        startup = subprocess.STARTUPINFO()
        startup.dwFlags |= subprocess.STARTF_USESHOWWINDOW
        startup.wShowWindow = 0
    with (tmp_path / "inkscape.log").open("w", encoding="utf-8") as log:
        process = subprocess.Popen(
            [str(executable_path), "--app-id-tag=" + namespace, "--with-gui", str(document)],
            env=dict(os.environ),
            stdout=log,
            stderr=log,
            startupinfo=startup,
        )
        try:
            deadline = time.monotonic() + 45
            while not connection.is_available(force=True):
                if process.poll() is not None or time.monotonic() >= deadline:
                    pytest.fail(
                        "Dedicated Inkscape did not become ready; see "
                        + str(tmp_path / "inkscape.log")
                    )
                time.sleep(0.2)
            result = connection.execute_operation(
                {
                    "operation": "get-info",
                    "attributes": {},
                }
            )
            assert result.get("data", {}).get("dimensions") == {"width": "320", "height": "200"}, (
                "Dedicated document not verified"
            )
            yield LiveDocument(connection, tmp_path)
        finally:
            if process.poll() is None:
                process.terminate()
                try:
                    process.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait(timeout=5)
            # Remove only this generated IPC namespace; never clean the shared parent.
            if (
                ipc.exists()
                and not ipc.is_symlink()
                and ipc.resolve().parent == expected_parent.resolve()
                and ipc.name == namespace
            ):
                shutil.rmtree(ipc)
