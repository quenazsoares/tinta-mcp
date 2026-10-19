"""Worker for MCP stdio integration; only addresses the test-owned service."""

import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from tintamcp import server  # noqa: E402

if __name__ == "__main__":
    service = os.environ["TINTA_TEST_SERVICE"]
    if not service.startswith("org.inkscape.Inkscape.TintaTest"):
        raise SystemExit("Refusing a non-test D-Bus service")
    connection = server.InkscapeConnection()
    connection.dbus_service = service
    connection.dbus_path = "/" + service.replace(".", "/")
    server._inkscape_connection = connection
    server.main()
