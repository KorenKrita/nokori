"""Setuptools hooks: prefetch local embed weights after pip install with [local-embed]."""
from __future__ import annotations

from setuptools.command.develop import develop
from setuptools.command.install import install


def _prefetch_after_pip() -> None:
    try:
        from .prefetch import maybe_prefetch_local_embed

        maybe_prefetch_local_embed()
    except Exception as e:
        print(f"nokori: post-install embed prefetch skipped: {e}")


class InstallWithPrefetch(install):
    """``pip install .[local-embed]`` (non-editable)."""

    def run(self) -> None:
        super().run()
        if self.root:
            return
        _prefetch_after_pip()


class DevelopWithPrefetch(develop):
    """``pip install -e .[local-embed]`` (editable)."""

    def run(self) -> None:
        super().run()
        _prefetch_after_pip()
