def _resolve_version() -> str:
    from importlib.metadata import PackageNotFoundError, version as _v

    # Prefer installed package metadata (reliable in installed-package scenarios)
    try:
        return _v("nokori")
    except PackageNotFoundError:
        pass

    # Fallback: pyproject.toml next to source (works in dev/editable installs)
    from pathlib import Path as _P
    import re as _re

    _pyproject = _P(__file__).resolve().parent.parent / "pyproject.toml"
    if _pyproject.exists():
        _m = _re.search(r'^version\s*=\s*"([^"]+)"', _pyproject.read_text(), _re.M)
        if _m:
            return _m.group(1)

    return "0.0.0"


__version__ = _resolve_version()
del _resolve_version
