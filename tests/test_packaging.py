"""Ensure release wheels bundle the built web UI."""

from __future__ import annotations

import zipfile
from pathlib import Path

from setuptools import Distribution

from nokori._packaging import InstallWithPrefetch



def test_wheel_includes_web_static(tmp_path: Path) -> None:
    import subprocess
    import sys

    root = Path(__file__).resolve().parents[1]
    wheel_dir = tmp_path / "wheels"
    subprocess.run(
        [sys.executable, "-m", "pip", "wheel", str(root), "-w", str(wheel_dir), "-q"],
        check=True,
        cwd=root,
    )
    wheels = list(wheel_dir.glob("nokori-*.whl"))
    assert wheels, "expected a nokori wheel"
    with zipfile.ZipFile(wheels[0]) as zf:
        names = zf.namelist()
    assert "nokori/web/static/index.html" in names
    assert any(n.startswith("nokori/web/static/assets/") for n in names)


def test_install_hook_skips_prefetch_when_installing_into_wheel_root(monkeypatch) -> None:
    calls: list[str] = []

    monkeypatch.setattr("setuptools.command.install.install.run", lambda self: calls.append("install"))
    monkeypatch.setattr("nokori._packaging._prefetch_after_pip", lambda: calls.append("prefetch"))

    cmd = InstallWithPrefetch(Distribution())
    cmd.root = "/tmp/wheel-staging"
    cmd.run()

    assert calls == ["install"]


def test_install_hook_prefetches_for_real_environment_install(monkeypatch) -> None:
    calls: list[str] = []

    monkeypatch.setattr("setuptools.command.install.install.run", lambda self: calls.append("install"))
    monkeypatch.setattr("nokori._packaging._prefetch_after_pip", lambda: calls.append("prefetch"))

    cmd = InstallWithPrefetch(Distribution())
    cmd.root = None
    cmd.run()

    assert calls == ["install", "prefetch"]
