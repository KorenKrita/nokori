from __future__ import annotations

import argparse

from ..config import Config


def run(_args: argparse.Namespace, cfg: Config) -> int:
    paths = [
        ("hook", cfg.logs_dir / "hook.log"),
        ("pipeline", cfg.logs_dir / "pipeline.log"),
        ("async-extract", cfg.logs_dir / "async-extract.log"),
    ]
    any_shown = False
    for label, path in paths:
        if not path.exists():
            continue
        any_shown = True
        print(f"=== {label} ({path}) ===")
        try:
            content = path.read_text(encoding="utf-8", errors="replace")
        except OSError as e:
            print(f"  (read error: {e})")
            continue
        lines = content.splitlines()
        for line in lines[-50:]:
            print(line)
    if not any_shown:
        print("(no log files yet)")
    return 0
