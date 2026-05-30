from __future__ import annotations

import argparse
import sys

from ..config import Config
from ..search import embed_ipc
from ..search.embedding_server import run_server as run_embed_server


def run(args: argparse.Namespace, cfg: Config) -> int:
    action = args.embed_action
    if action == "serve":
        return run_embed_server(cfg)
    if action == "stop":
        stopped = embed_ipc.stop_server(cfg)
        if stopped:
            print("embed server stopped")
            return 0
        print("embed server was not running")
        return 0
    if action == "status":
        st = embed_ipc.server_status(cfg)
        print(f"embed.running   {st['running']}")
        print(f"embed.pid       {st['pid']}")
        print(f"embed.socket    {st['socket']}")
        print(f"embed.idle_s    {st['idle_seconds']}")
        return 0
    if action == "start":
        if embed_ipc.ensure_running(cfg):
            print("embed server ready")
            return 0
        print("nokori: embed server failed to start (see logs/embed-server.log)", file=sys.stderr)
        return 1
    print(f"nokori: unknown embed action {action!r}", file=sys.stderr)
    return 2
