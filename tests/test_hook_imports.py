"""Verify all hook modules and their cross-module imports resolve without error.

Regression test for _spawn_async_extract going missing after refactor (cd2e7ab9).
"""
from __future__ import annotations

import ast
import importlib
import pkgutil
from pathlib import Path

import nokori.hooks


def test_all_hook_modules_importable():
    """Every module under nokori.hooks must import without ImportError."""
    failures = []
    for _importer, modname, _ispkg in pkgutil.iter_modules(nokori.hooks.__path__):
        try:
            importlib.import_module(f"nokori.hooks.{modname}")
        except Exception as e:
            failures.append((modname, e))
    assert not failures, f"Hook modules failed to import: {failures}"


def test_cross_module_imports_resolve():
    """All intra-package 'from .X import Y' in hooks/ must resolve at import time."""
    hooks_dir = Path(nokori.hooks.__path__[0])
    imports = []
    for f in sorted(hooks_dir.glob("*.py")):
        tree = ast.parse(f.read_text())
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and node.level >= 1 and node.module:
                imports.extend(
                    (f.name, node.level, node.module, alias.name)
                    for alias in node.names
                )

    # nokori.hooks is the base; level 1 = nokori.hooks.{module},
    # level 2 = nokori.{module}, etc.
    base_parts = ["nokori", "hooks"]
    failures = []
    for source, level, module, name in imports:
        parent_parts = base_parts[: len(base_parts) - (level - 1)]
        fqn = ".".join(parent_parts + [module])
        mod = importlib.import_module(fqn)
        if not hasattr(mod, name):
            failures.append(f"{source}: from {'.' * level}{module} import {name} — name not found")

    assert not failures, "\n".join(failures)


def test_version_single_source():
    """__version__ must match pyproject.toml — ensures single source of truth."""
    import re

    from nokori import __version__

    pyproject = Path(__file__).resolve().parent.parent / "pyproject.toml"
    m = re.search(r'^version\s*=\s*"([^"]+)"', pyproject.read_text(), re.M)
    assert m, "Could not find version in pyproject.toml"
    assert __version__ == m.group(1), (
        f"__version__={__version__!r} != pyproject.toml={m.group(1)!r}"
    )
