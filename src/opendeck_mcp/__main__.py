"""opendeck-mcp: stdio by default; `serve` for HTTP; helpers for the token and the logon task."""

from __future__ import annotations

import argparse


def _paths():
    from .core.paths import discover

    return discover()


def main(argv: list[str] | None = None) -> None:
    from . import __version__
    from . import http as H

    parser = argparse.ArgumentParser(prog="opendeck-mcp", description="MCP server for OpenDeck.")
    parser.add_argument("--version", action="version", version=__version__)
    sub = parser.add_subparsers(dest="command")
    sub.add_parser("stdio", help="serve over stdio (the default; what `claude mcp add` starts)")

    def http_options(p: argparse.ArgumentParser) -> None:
        p.add_argument("--host", default="127.0.0.1",
                       help="address to listen on, e.g. this PC's Tailscale IP (default 127.0.0.1)")
        p.add_argument("--port", type=int, default=H.DEFAULT_PORT)
        p.add_argument("--allowed-host", action="append", default=[], metavar="NAME",
                       help="extra Host name clients may use, e.g. a MagicDNS name (repeatable)")
        p.add_argument("--allow-all-interfaces", action="store_true",
                       help="permit --host 0.0.0.0 / :: (every network the PC is on)")

    http_options(sub.add_parser("serve", help="serve over HTTP for clients on other machines"))
    token = sub.add_parser("token", help="print the HTTP token and the matching `claude mcp add` command")
    token.add_argument("--reset", action="store_true", help="make a new token (old clients stop working)")
    token.add_argument("--host", default="127.0.0.1")
    token.add_argument("--port", type=int, default=H.DEFAULT_PORT)
    task = sub.add_parser("install-autostart",
                          help="Windows: start the HTTP server hidden at every logon, and now")
    http_options(task)
    task.add_argument("--from", dest="source", default=f"git+https://github.com/kastro5/opendeck-mcp@v{__version__}",
                      help="what uvx runs (default: this release); 'local' runs this Python environment instead")
    sub.add_parser("uninstall-autostart", help="Windows: remove autostart and stop the running HTTP server")
    args = parser.parse_args(argv)

    if args.command in (None, "stdio"):
        from .server import run

        run()
    elif args.command == "serve":
        H.serve(_paths(), args.host, args.port, args.allow_all_interfaces, args.allowed_host)
    elif args.command == "token":
        value = H.get_token(_paths(), reset=args.reset)
        print(("New token. " if args.reset else "") + "On the client machine run:\n\n  "
              + H.client_command(args.host, args.port, value)
              + "\n\nKeep the token private: it lets anyone who can reach the port edit your OpenDeck.")
    elif args.command == "install-autostart":
        H.check_bind(args.host, args.allow_all_interfaces)
        source = None if args.source == "local" else args.source
        command = H.server_command(source, args.host, args.port, args.allowed_host, args.allow_all_interfaces)
        H.get_token(_paths())
        H.stop_servers()
        script = H.install_autostart(command)
        H.start_autostart()
        print(f"Autostart installed ({script}) and started: http://{args.host}:{args.port}/mcp\n"
              f"Get the client command with: opendeck-mcp token --host {args.host} --port {args.port}")
    elif args.command == "uninstall-autostart":
        removed = H.uninstall_autostart()
        stopped = H.stop_servers()
        print(("Removed autostart" if removed else "Autostart wasn't installed")
              + (f"; stopped {stopped} running server process(es)." if stopped else "."))


if __name__ == "__main__":
    main()
