import sys

from .cli import main as _main


def main(argv: list[str] | None = None) -> int:
    return _main(argv if argv is not None else sys.argv[1:])


if __name__ == "__main__":
    raise SystemExit(main())
