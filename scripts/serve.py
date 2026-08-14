#!/usr/bin/env python3
"""Serve the static dashboard on localhost."""

from __future__ import annotations

import argparse
import http.server
import socketserver
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
DASHBOARD = ROOT / "output" / "market_lag_dashboard"


class ReusableTCPServer(socketserver.TCPServer):
    allow_reuse_address = True


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", type=int, default=8012)
    args = parser.parse_args()
    handler = lambda *handler_args, **handler_kwargs: http.server.SimpleHTTPRequestHandler(
        *handler_args,
        directory=str(DASHBOARD),
        **handler_kwargs,
    )
    with ReusableTCPServer(("127.0.0.1", args.port), handler) as server:
        print(f"http://127.0.0.1:{args.port}/")
        server.serve_forever()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
