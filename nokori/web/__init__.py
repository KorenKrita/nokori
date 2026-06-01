from __future__ import annotations

import argparse
import sys


def run(args: argparse.Namespace, cfg) -> int:
    try:
        import uvicorn  # noqa: F401
    except ImportError:
        print(
            "nokori: web UI requires extra dependencies. "
            "Install with: pip install nokori[web]",
            file=sys.stderr,
        )
        return 1

    from .app import create_app

    app = create_app(cfg)
    port = getattr(args, "port", 8765)
    no_browser = getattr(args, "no_browser", False)

    if not no_browser:
        import threading
        import webbrowser

        threading.Timer(1.0, webbrowser.open, args=(f"http://localhost:{port}",)).start()

    uvicorn.run(app, host="127.0.0.1", port=port, log_level="warning")
    return 0
