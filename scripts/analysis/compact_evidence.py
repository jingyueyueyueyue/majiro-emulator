"""Share byte-identical screenshots from this project's dated native checks."""
from __future__ import annotations
import os
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "convert"))
from port_common import ROOT, sha256, write_json
from storage_audit import walk_files


def run():
    archive = ROOT / "archive"
    canonical = {}
    rows = []
    active = {p.parent for p in walk_files(archive) if p.name == "analysis-active.json"}
    def busy(path):
        return any(parent == root for parent in path.parents for root in active)
    # Include benchmarks, timed frame checks and contact sheets as well.
    # walk_files excludes junctions, so archived probe links cannot lead
    # back into shared game resources or a live player's directory.
    for path in sorted(p for p in walk_files(archive)if p.suffix.lower()in(".png",".mjb",".mjs")):
        if busy(path):
            continue
        if path.is_symlink() or path.parent.is_junction():
            raise ValueError("unexpected evidence link")
        try:
            digest = sha256(path)
        except FileNotFoundError:
            # A newly started probe can recycle an immutable save chunk
            # between enumeration and hashing. Its live index is not edited.
            continue
        if digest not in canonical:
            canonical[digest] = path
            continue
        source = canonical[digest]
        if os.path.samefile(source, path):
            continue
        temporary = path.with_name(path.name + ".link-tmp")
        try:
            os.link(source, temporary)
            os.replace(temporary, path)
        finally:
            if temporary.exists():
                temporary.unlink()
        rows.append({"path": path.relative_to(ROOT).as_posix(), "bytes_shared": path.stat().st_size})
    report = {"status": "passed", "deduplicated": rows,
              "bytes_shared_this_run": sum(row["bytes_shared"] for row in rows),
              "active_evidence_skipped": [str(p.relative_to(ROOT)) for p in sorted(active)],
              "method": "hardlinks; screenshot and content-addressed test-save hashes preserved", "copy_fallback": False}
    write_json(ROOT / "reports/evidence_compaction.json", report)
    return report


if __name__ == "__main__":
    print(run())
