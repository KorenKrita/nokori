from __future__ import annotations

import argparse
from collections import deque

from ..config import Config


def run(_args: argparse.Namespace, cfg: Config) -> int:
    paths = [
        ("hook", cfg.logs_dir / "hook.log"),
        ("pipeline", cfg.logs_dir / "pipeline.log"),
        ("async-extract", cfg.logs_dir / "async-extract.log"),
        ("embed-server", cfg.logs_dir / "embed-server.log"),
    ]
    any_shown = False
    for label, path in paths:
        if not path.exists():
            continue
        any_shown = True
        print(f"=== {label} ({path}) ===")
        try:
            with path.open(encoding="utf-8", errors="replace") as fh:
                tail = deque(fh, maxlen=50)
        except OSError as e:
            print(f"  (read error: {e})")
            continue
        for line in tail:
            print(line.rstrip("\n"))
    if not any_shown:
        print("(no log files yet)")
    return 0
