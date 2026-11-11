"""Isolate native tests with hardlinks/junctions; never duplicate game assets."""
from __future__ import annotations
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "build"))
from build_windows import hardlink, junction


def isolated_project(source: Path, destination: Path):
    destination.mkdir(parents=True, exist_ok=False)
    for name in ("runtime", "scripts", "native", "image", "audio", "movie", "font", "config", "system"):
        junction(destination/name, source/name)
    for name in ("Ruri_Artemis.exe", "system.ini"):
        hardlink(source/name, destination/name)
    (destination/"savedata-dev").mkdir()
    return destination


def clean_project(project: Path):
    """Detach only the exact links we created; retain archived diagnostics."""
    for name in ("runtime", "scripts", "native", "image", "audio", "movie", "font", "config", "system"):
        link=project/name
        if link.is_junction():
            link.rmdir()
    for name in ("Ruri_Artemis.exe","system.ini"):
        path=project/name
        if path.is_file():
            path.unlink()
    for path in (project/"savedata-dev").glob("ruri_live_*.*"):
        if path.suffix not in (".png",".bmp"):
            continue
        path.unlink()
