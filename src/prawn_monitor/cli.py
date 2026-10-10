"""Command line for the hub, a tank node, and the simulated demo."""

from __future__ import annotations

import argparse
import logging
from pathlib import Path

from prawn_monitor.demo import format_report, run_check, serve_until_interrupt
from prawn_monitor.hub import HubApp, load_config
from prawn_monitor.httputil import serve
from prawn_monitor.node import NodeApp


def _block(handle, *closers) -> int:
    try:
        while handle.thread.is_alive():
            handle.thread.join(timeout=0.5)
    except KeyboardInterrupt:
        print("\nStopping.")
    finally:
        handle.close()
        for closer in closers:
            closer()
    return 0


def cmd_hub(args: argparse.Namespace) -> int:
    config = load_config(args.config)
    app = HubApp(config)
    host = config.get("host", "0.0.0.0")
    handle = serve(app, host, int(config.get("port", 8080)))
    app.public_url = app.public_url or f"http://127.0.0.1:{handle.port}"
    print(f"Prawn Monitor hub at {app.public_url}")
    return _block(handle, app.close)


def cmd_node(args: argparse.Namespace) -> int:
    config = load_config(args.config)
    if args.simulate:
        config.setdefault("sensors", {})["mode"] = "simulated"
        config.setdefault("camera", {})["driver"] = "simulated"
    app = NodeApp(config)
    host = config.get("host", "0.0.0.0")
    handle = serve(app, host, int(config.get("port", 8090)))
    print(f"Prawn Monitor node {app.tank_id} at http://{host}:{handle.port}")
    return _block(handle, app.close)


def cmd_demo(args: argparse.Namespace) -> int:
    root = Path(args.data_dir)
    root.mkdir(parents=True, exist_ok=True)
    if args.serve:
        serve_until_interrupt(root)
        return 0
    report = run_check(root)
    print(format_report(report))
    return 0 if report["ok"] else 1


def main(argv: list[str] | None = None) -> int:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s %(message)s")
    parser = argparse.ArgumentParser(prog="prawn-monitor", description="Local shrimp-tank monitor")
    commands = parser.add_subparsers(dest="cmd", required=True)

    hub = commands.add_parser("hub", help="poll tank nodes and serve the dashboard")
    hub.add_argument("--config", required=True)
    hub.set_defaults(func=cmd_hub)

    node = commands.add_parser("node", help="read probes and serve the camera for one tank")
    node.add_argument("--config", required=True)
    node.add_argument("--simulate", action="store_true", help="ignore hardware and use simulated probes")
    node.set_defaults(func=cmd_node)

    demo = commands.add_parser("demo", help="run two simulated tanks against a local hub")
    demo.add_argument("--check", action="store_true", help="run the scenario and exit")
    demo.add_argument("--serve", action="store_true", help="run the scenario and leave the dashboard up")
    demo.add_argument("--data-dir", default="var/demo")
    demo.set_defaults(func=cmd_demo)

    args = parser.parse_args(argv)
    return int(args.func(args))
