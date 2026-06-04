"""Ensure release wheels bundle the built web UI."""

from __future__ import annotations

import zipfile
from pathlib import Path



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
