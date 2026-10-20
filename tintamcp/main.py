#!/usr/bin/env python3
"""Command-line entry point for the Tinta server and installer."""

import argparse
import sys

from tintamcp import installer


def main(arguments: list[str] | None = None) -> None:
    if sys.platform == "win32":
        for stream in (sys.stdout, sys.stderr):
            reconfigure = getattr(stream, "reconfigure", None)
            if callable(reconfigure):
                try:
                    reconfigure(encoding="utf-8")
                except (OSError, ValueError):
                    pass
    arguments = sys.argv[1:] if arguments is None else arguments
    parser = argparse.ArgumentParser(
        prog="tinta-mcp",
        description="Connect AI assistants to Inkscape through MCP.\n"
        "Run without a command to start the server.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    commands = parser.add_subparsers(dest="command", title="commands")
    install = commands.add_parser(
        "install",
        help="Install the extension and set up supported clients",
        description="Install the Tinta extension and set up supported clients.",
    )
    installer.add_install_arguments(install)
    if any(argument.split("=", 1)[0] == "--enable-python" for argument in arguments):
        parser.error("--enable-python was removed; Tinta supports structured SVG operations only.")
    options = parser.parse_args(arguments)
    if options.command == "install":
        installer.run_installer(
            auto_yes=options.yes,
            agents=tuple(options.agent),
            dry_run=options.dry_run,
            only=options.only,
        )
    else:
        from tintamcp.server import main as run_server

        run_server([])


if __name__ == "__main__":
    main()
