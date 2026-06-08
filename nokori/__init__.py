def _resolve_version() -> str:
    from pathlib import Path as _P
    import re as _re

    # Prefer pyproject.toml next to source when running from a checkout. Editable
    # installs can leave package metadata stale after version-only commits.
    _pyproject = _P(__file__).resolve().parent.parent / "pyproject.toml"
    if _pyproject.exists():
        _m = _re.search(r'^version\s*=\s*"([^"]+)"', _pyproject.read_text(), _re.M)
        if _m:
            return _m.group(1)

    from importlib.metadata import PackageNotFoundError, version as _v

    # Installed wheels/sdists do not include the repository pyproject next to the
    # package, so metadata remains the reliable installed-package source.
    try:
        return _v("nokori")
    except PackageNotFoundError:
        pass

    return "0.0.0"


__version__ = _resolve_version()
del _resolve_version
