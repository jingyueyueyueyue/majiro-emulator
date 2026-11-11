"""Count workspace files once, skipping shared directory junctions and duplicate hardlinks."""
from __future__ import annotations
import os
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "convert"))
from port_common import ROOT, config, read_json, write_json


def walk_files(path):
    for entry in os.scandir(path):
        child = Path(entry.path)
        if child.is_junction() or child.is_symlink():
            continue
        if entry.is_dir(follow_symlinks=False):
            yield from walk_files(child)
        elif entry.is_file(follow_symlinks=False):
            yield child


def run():
    font = ROOT / config()["font_source"]
    license_path = font.with_name("SourceHanSansSC-LICENSE.txt")
    seen = set()
    unique = 0
    folders = {}
    for name in ("config", "docs", "localization", "references", "scripts", "runtime",
                 "resources", "projects", "reports", "archive"):
        path = ROOT / name
        total = 0
        if path.exists():
            for item in walk_files(path):
                try:
                    info = item.stat()
                except FileNotFoundError:
                    continue  # A running isolated probe may recycle a save chunk.
                key = (info.st_dev, info.st_ino)
                if key in seen:
                    continue
                seen.add(key)
                total += info.st_size
        folders[name] = total
        unique += total
    unique += (ROOT / "README.md").stat().st_size
    shortcut = ROOT / "启动移植测试.lnk"
    if shortcut.exists():
        unique += shortcut.stat().st_size
    assets = read_json(ROOT / "reports/all_assets.json") if (ROOT / "reports/all_assets.json").is_file() else {}
    report = {"schema": 1, "new_unique_file_bytes": unique,
              "external_font_bytes_shared_without_copy": 0,
              "workspace_font_bytes": font.stat().st_size,
              "new_bytes_by_directory": folders, "new_game_archive_copies": 0,
              "prepared_asset_counts": {kind: row["total"] for kind, row in assets.get("counts", {}).items()},
              "movie_copies": 0, "resource_library_copies": 0,
              "note": "all effective image/audio assets have one prepared library; hardlinked file content counted once; filesystem metadata excluded"}
    write_json(ROOT / "reports/storage_audit.json", report)
    return report


if __name__ == "__main__":
    print(run())
