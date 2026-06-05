def _resolve_version() -> str:
    from pathlib import Path as _P
    import re as _re

    # Prefer pyproject.toml next to source (works in dev and editable installs)
    _pyproject = _P(__file__).resolve().parent.parent / "pyproject.toml"
    if _pyproject.exists():
        _m = _re.search(r'^version\s*=\s*"([^"]+)"', _pyproject.read_text(), _re.M)
        if _m:
            return _m.group(1)

    # Fallback: installed package metadata
    from importlib.metadata import PackageNotFoundError, version as _v
    try:
        return _v("nokori")
    except PackageNotFoundError:
        return "0.0.0"


__version__ = _resolve_version()
del _resolve_version
