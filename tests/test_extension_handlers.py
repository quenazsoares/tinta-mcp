"""Exercise extension handlers in Inkscape's interpreter on disposable SVGs."""

import os
import subprocess
import sys
from pathlib import Path

import pytest


def test_extension_handlers_in_inkex():
    if sys.platform == "win32":
        python = Path(
            os.environ.get("TINTA_TEST_INKSCAPE_PYTHON", "C:/Program Files/Inkscape/bin/python.exe")
        )
        inkex_path = python.parent.parent / "share/inkscape/extensions"
    else:
        python = Path(os.environ.get("TINTA_TEST_INKSCAPE_PYTHON", "/usr/bin/python3"))
        inkex_path = Path("/usr/share/inkscape/extensions")
    inkex_path = Path(os.environ.get("TINTA_INKEX_PATH", str(inkex_path)))
    if not python.is_file() or not (inkex_path / "inkex").is_dir():
        pytest.skip("Inkscape Python/inkex unavailable for isolated extension integration")
    result = subprocess.run(
        [str(python), "-B", str(Path(__file__).with_name("inkex_cases.py")), "-v"],
        env={**os.environ, "TINTA_INKEX_PATH": str(inkex_path), "PYTHONIOENCODING": "utf-8"},
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=30,
    )
    assert result.returncode == 0, result.stdout + result.stderr
