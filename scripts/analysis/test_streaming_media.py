"""Run production media extraction below the resource size and inventory real large members."""
from pathlib import Path
import json
import subprocess
import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "convert"))
from port_common import ROOT, archive_dir, read_json, sha256, timestamp, write_json
from majiro_arc import Archive


def main():
    folder = archive_dir("streaming_media")
    source = ROOT / "player/android/src/org/majiro/player"
    java = Path(read_json(ROOT / "reports/android_toolchain.json")["java"]) / "bin"
    sources = [source / "MediaCache.java", source / "MediaSliceInputStream.java",
               ROOT / "scripts/analysis/StreamingMediaChecks.java"]
    for label, command in (
        ("compile", [str(java / "javac.exe"), "-encoding", "UTF-8", "-d", str(folder), *map(str, sources)]),
        ("run", [str(java / "java.exe"), "-Xmx24m", "-cp", str(folder),
                 "org.majiro.player.StreamingMediaChecks", str(folder / "files")]),
    ):
        result = subprocess.run(command, capture_output=True, timeout=180, cwd=folder)
        (folder / (label + ".txt")).write_bytes(result.stdout + result.stderr)
        if result.returncode:
            raise RuntimeError(label + " failed: " + str(folder / (label + ".txt")))
    physical = json.loads(result.stdout)
    large = []
    archives = 0
    for game in read_json(ROOT / "config/recovered_library.json")["games"]:
        for path in Path(game["directory"]).rglob("*.arc"):
            if any(part.lower() in ("archive", "savedata", "save_chs", ".workbuddy") for part in path.relative_to(game["directory"]).parts[:-1]):
                continue
            archive = Archive(path)
            archives += 1
            for member in archive.members:
                if member.size > 64 * 1024 * 1024 and member.extension in (".wav", ".ogg", ".mp3", ".wma"):
                    large.append({"game": game["id"], "archive": str(path), "name": member.name,
                                  "offset": member.offset, "bytes": member.size})
    assert any(row["game"] == "narci_sumire" for row in large), "reported resource is absent from inventory"
    # This also guards the JNI routing, whose descriptors are checked by packaging.
    host = (ROOT / "player/android/game_host.cpp").read_text("utf8")
    asset = host.split("int media_asset(lua_State* state) {", 1)[1].split("\nint audio(", 1)[0]
    assert "read_member(" not in asset and "byte_array(" not in asset
    assert 'method("cacheMediaRegion"' in asset and 'method("cacheMediaFile"' in asset
    report = {"status": "passed", "timestamp": timestamp(), "physical": physical,
              "archives_scanned": archives, "large_audio": large,
              "source_sha256": {str(p.relative_to(ROOT)): sha256(p) for p in sources},
              "device_accessed": False, "evidence": str(folder.relative_to(ROOT)),
              "scope": "real production Java streaming/cache/range with 24 MiB heap, 96 MiB resource, corruption, eviction and failures; real ARC large-audio inventory"}
    write_json(ROOT / "reports/streaming_media_tests.json", report)
    write_json(folder / "report.json", report)
    # Keep receipts and logs; synthetic large fixtures are rebuildable scratch.
    import shutil
    scratch = (folder / "files").resolve()
    assert scratch.is_relative_to((ROOT / "archive").resolve())
    shutil.rmtree(scratch)
    print(json.dumps({"status": "passed", "physical": physical, "large_audio_members": len(large),
                      "evidence": report["evidence"]}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
