"""Fetch only small pinned format references; never clone a repository or fetch game files."""
from __future__ import annotations
import hashlib
from pathlib import Path
import sys
import urllib.request

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "convert"))
from port_common import ROOT, atomic_bytes, read_json, write_json

COMMIT = "f7ec450d5b78f0ed6df603b8c0b0ed79748d9cee"
BASE = f"https://raw.githubusercontent.com/trigger-segfault/majiro-py/{COMMIT}/"
FILES = {
    "opcodes.py": "src/mjotool/opcodes.py",
    "flags.py": "src/mjotool/flags.py",
    "known_hashes.json": "data/known_hashes_compact.json",
    "LICENSE": "LICENSE",
    "README.md": "README.md",
}


def fetch(verify_only=False):
    lock_path = ROOT / "config/references.lock.json"
    existing = read_json(lock_path) if lock_path.exists() else None
    records = []
    for name, upstream in FILES.items():
        path = ROOT / "references/majiro-py" / name
        if not path.exists():
            if verify_only:
                raise FileNotFoundError(path)
            with urllib.request.urlopen(BASE + upstream, timeout=25) as response:
                content = response.read(1024 * 1024 + 1)
            if len(content) > 1024 * 1024:
                raise ValueError("reference exceeds 1 MiB limit")
            atomic_bytes(path, content)
        content = path.read_bytes()
        record = {"file": str(path.relative_to(ROOT)).replace("\\", "/"),
                  "sha256": hashlib.sha256(content).hexdigest(), "bytes": len(content),
                  "url": BASE + upstream}
        if existing:
            expected = next(x for x in existing["files"] if x["file"] == record["file"])
            if record != expected:
                raise ValueError(f"pinned reference changed: {name}")
        records.append(record)
    result = {"repository": "trigger-segfault/majiro-py", "commit": COMMIT, "files": records}
    if not verify_only:
        write_json(lock_path, result)
    return result


if __name__ == "__main__":
    print(fetch("--verify-only" in sys.argv))
