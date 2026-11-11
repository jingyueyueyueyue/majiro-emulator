"""Pin the referenced Android media runtime and its upstream Java interface."""
from __future__ import annotations
from concurrent.futures import ThreadPoolExecutor
import hashlib
import json
from pathlib import Path
import sys
import urllib.request

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "convert"))
from port_common import ROOT, atomic_bytes, timestamp, write_json


def request(url):
    req = urllib.request.Request(url, headers={"User-Agent": "MajiroBuild/1.0"})
    for attempt in range(3):
        try:
            with urllib.request.urlopen(req, timeout=45) as response:
                return response.read()
        except Exception:
            if attempt == 2:
                raise


def tree(repository):
    metadata = json.loads(request("https://api.github.com/repos/" + repository))
    commit = json.loads(request("https://api.github.com/repos/" + repository + "/commits/" + metadata["default_branch"]))["sha"]
    listing = json.loads(request("https://api.github.com/repos/" + repository + "/git/trees/" + commit + "?recursive=1"))
    return commit, listing["tree"]


def main():
    root = ROOT / "references/player/ijk"
    native_commit, native_tree = tree("Weiss-UltimateSavior/Tyranor-Next")
    upstream_commit, upstream_tree = tree("bilibili/ijkplayer")
    files = []
    for row in native_tree:
        path = row["path"]
        if path in {
            "engine/src/main/jniLibs/arm64-v8a/libijkffmpeg.so",
            "engine/src/main/jniLibs/arm64-v8a/libijksdl.so",
            "engine/src/main/jniLibs/arm64-v8a/libijkplayer.so",
        }:
            files.append(("Weiss-UltimateSavior/Tyranor-Next", native_commit, row,
                          root / "arm64-v8a" / Path(path).name))
    prefix = "android/ijkplayer/ijkplayer-java/src/main/java/"
    for row in upstream_tree:
        path = row["path"]
        if row["type"] == "blob" and path.startswith(prefix) and path.endswith(".java"):
            files.append(("bilibili/ijkplayer", upstream_commit, row, root / "java" / path[len(prefix):]))
        elif path in {"COPYING.LGPLv2.1", "COPYING.GPLv2", "COPYING.GPLv3", "COPYING.LGPLv3", "README.md"}:
            files.append(("bilibili/ijkplayer", upstream_commit, row, root / "licenses" / Path(path).name))
    if sum(str(target).endswith(".so") for _, _, _, target in files) != 3:
        raise ValueError("pinned media runtime libraries were not found")

    def fetch(item):
        repository, commit, row, target = item
        url = "https://raw.githubusercontent.com/" + repository + "/" + commit + "/" + row["path"]
        data = request(url)
        git_hash = hashlib.sha1(b"blob " + str(len(data)).encode("ascii") + b"\0" + data).hexdigest()
        if git_hash != row["sha"]:
            raise ValueError("pinned Git blob integrity mismatch: " + row["path"])
        atomic_bytes(target, data)
        return {"repository": repository, "commit": commit, "path": row["path"],
                "local": str(target.relative_to(ROOT)), "git_blob": row["sha"],
                "sha256": hashlib.sha256(data).hexdigest(), "bytes": len(data)}
    with ThreadPoolExecutor(max_workers=6) as pool:
        records = list(pool.map(fetch, files))
    write_json(ROOT / "config/player_media.lock.json", {
        "schema": 1, "timestamp": timestamp(), "files": records,
        "native_origin": "prebuilt media libraries in the user-referenced Tyranor-Next repository",
        "java_origin": "bilibili/ijkplayer upstream interface sources",
        "native_rebuilt_from_source": False,
    })
    print("Pinned IJK media runtime and upstream Java:", len(records), "files")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
