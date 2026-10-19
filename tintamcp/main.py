#!/usr/bin/env python3
"""
Entry point for Inkscape MCP Server
"""

import sys

if sys.platform == "win32":
    # Ensure stdout/stderr use UTF-8 on Windows to avoid cp1252 encoding errors with emojis
    try:
        reconfigure_stdout = getattr(sys.stdout, "reconfigure", None)
        if callable(reconfigure_stdout):
            reconfigure_stdout(encoding="utf-8")
        reconfigure_stderr = getattr(sys.stderr, "reconfigure", None)
        if callable(reconfigure_stderr):
            reconfigure_stderr(encoding="utf-8")
    except Exception:
        pass

if any(argument.split("=", 1)[0] == "--enable-python" for argument in sys.argv[1:]):
    sys.stderr.write(
        "error: --enable-python was removed; Tinta supports structured SVG operations only.\n"
    )
    sys.exit(2)

if len(sys.argv) > 1 and sys.argv[1] == "install":
    from tintamcp.installer import run_installer

    auto_yes = "--yes" in sys.argv or "-y" in sys.argv
    run_installer(auto_yes=auto_yes)
    sys.exit(0)

from tintamcp.server import main

if __name__ == "__main__":
    main()
