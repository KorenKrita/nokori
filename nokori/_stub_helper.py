from __future__ import annotations

import sys


def not_yet(name: str) -> int:
    print(f"nokori: '{name}' not yet implemented", file=sys.stderr)
    return 0
