"""A requested live run must fail instead of skipping when setup is missing."""

import os
import subprocess
import sys
import xml.etree.ElementTree as ET
from pathlib import Path


def test_missing_live_executable_is_a_failure(tmp_path):
    result_file = tmp_path / "junit.xml"
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "pytest",
            "tests/test_dbus_smoke.py::test_document_info",
            "--live-inkscape",
            "-q",
            "-o",
            "addopts=",
            "-p",
            "no:cacheprovider",
            "--junitxml=" + str(result_file),
        ],
        cwd=Path(__file__).resolve().parents[1],
        env={**os.environ, "TINTA_TEST_INKSCAPE": str(tmp_path / "missing-inkscape")},
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=30,
    )
    assert result.returncode != 0
    root = ET.parse(result_file).getroot()
    assert root.findall(".//error")
    assert not root.findall(".//skipped")
    assert "Inkscape executable is unavailable" in result.stdout
