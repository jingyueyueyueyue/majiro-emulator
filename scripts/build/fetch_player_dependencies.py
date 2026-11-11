"""Fetch pinned SDL3 Android and Lua sources with official archive checksums."""
from __future__ import annotations
import hashlib
import io
from pathlib import Path, PurePosixPath
import sys
import tarfile
import urllib.request
import zipfile

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "convert"))
from port_common import ROOT, atomic_bytes, timestamp, write_json

SDL_URL = "https://github.com/libsdl-org/SDL/releases/download/release-3.4.20/SDL3-devel-3.4.20-android.zip"
SDL_SHA = "535320e58f3bd7235b3e15f940595cdeeb9b8c0aa6b3810ff127345e3567d4da"
LUA_URL = "https://lua.org/ftp/lua-5.1.5.tar.gz"
LUA_SHA = "2640fc56a795f29d28ef15e13c34a47e223960b0240e8cb0a82d9b0738695333"


def get(url, expected):
    request = urllib.request.Request(url, headers={"User-Agent": "MajiroBuild/1.0"})
    with urllib.request.urlopen(request, timeout=60) as response:
        data = response.read()
    if hashlib.sha256(data).hexdigest() != expected:
        raise ValueError("dependency archive checksum mismatch: " + url)
    return data


def main():
    root = ROOT / "references/player"
    records = []
    # Reuse only an already verified archive from this task's earlier inspection.
    inspection = ROOT / "reports/player_dependency_inspection.json"
    archive = None
    if inspection.is_file():
        from port_common import read_json
        path = Path(read_json(inspection)["sdl_archive"])
        if path.is_file():
            candidate = path.read_bytes()
            if hashlib.sha256(candidate).hexdigest() == SDL_SHA:
                archive = candidate
    archive = archive or get(SDL_URL, SDL_SHA)
    written = []
    with zipfile.ZipFile(io.BytesIO(archive)) as package:
        atomic_bytes(root / "SDL3/LICENSE.txt", package.read("LICENSE.txt"))
        with zipfile.ZipFile(io.BytesIO(package.read("SDL3-3.4.20.aar"))) as aar:
            for entry in aar.infolist():
                name = entry.filename
                target = None
                prefix = "prefab/modules/SDL3-Headers/include/"
                if name.startswith(prefix) and not entry.is_dir():
                    relative = PurePosixPath(name[len(prefix):])
                    if relative.is_absolute() or ".." in relative.parts:
                        raise ValueError("invalid SDL header path")
                    target = root / "SDL3/include" / Path(*relative.parts)
                elif name == "classes.jar":
                    target = root / "SDL3/classes.jar"
                elif name == "prefab/modules/SDL3-shared/libs/android.arm64-v8a/libSDL3.so":
                    target = root / "SDL3/arm64-v8a/libSDL3.so"
                if target is not None:
                    atomic_bytes(target, aar.read(entry))
                    written.append(str(target.relative_to(ROOT)))
    records.append({"name": "SDL3", "version": "3.4.20", "url": SDL_URL,
                    "archive_sha256": SDL_SHA, "files": written})
    data = get(LUA_URL, LUA_SHA)
    written = []
    with tarfile.open(fileobj=io.BytesIO(data), mode="r:gz") as package:
        for entry in package.getmembers():
            name = PurePosixPath(entry.name)
            if (not entry.isfile() or not name.parts or name.parts[0] != "lua-5.1.5"
                    or ".." in name.parts):
                continue
            relative = PurePosixPath(*name.parts[1:])
            if relative.as_posix() == "COPYRIGHT" or (
                    relative.parts and relative.parts[0] == "src" and relative.suffix in (".c", ".h")):
                target = root / "lua" / Path(*relative.parts)
                atomic_bytes(target, package.extractfile(entry).read())
                written.append(str(target.relative_to(ROOT)))
    records.append({"name": "Lua", "version": "5.1.5", "url": LUA_URL,
                    "archive_sha256": LUA_SHA, "files": written})
    write_json(ROOT / "config/player_dependencies.lock.json", {
        "schema": 1, "timestamp": timestamp(), "dependencies": records})
    print("Pinned SDL3 Android ARM64 headers/runtime and Lua 5.1.5 sources ready")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
