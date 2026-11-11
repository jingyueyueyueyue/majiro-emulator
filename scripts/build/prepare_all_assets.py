"""Prepare every effective image/audio asset once; share existing files and original movies."""
import argparse
import time
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "convert"))
from port_common import ROOT, atomic_bytes, lua_value, read_json, write_json, sha256, timestamp
from majiro_arc import Catalogue
from majiro_image import Decoder
from majiro_audio import Audio
from majiro_movie import catalogue as movie_catalogue


def run(verify_only=False):
    source = Catalogue(ROOT)
    images = Decoder(source)
    audio = Audio(source)
    catalogues = {}
    for kind in ("image", "audio"):
        path = ROOT / ("resources/" + kind + "_catalogue.json")
        catalogues[kind] = read_json(path) if path.exists() else {}
    members = sorted((values[0] for values in source.lookup.values()
                      if values[0].extension in (".rct", ".rc8", ".ogg", ".wav")),
                     key=lambda m: m.name)
    digests = {}
    failures = []
    counts = {kind: {"total": 0, "reused": 0, "prepared": 0, "bytes": 0}
              for kind in catalogues}
    began = time.monotonic()
    def digest(member):
        if member.identity not in digests:
            digests[member.identity] = member.digest()
        return digests[member.identity]
    def valid(member, record):
        if not record:
            return False
        path = ROOT / "resources" / record["file"]
        if not path.is_file() or sha256(path) != record["sha256"]:
            return False
        if member.extension in (".ogg", ".wav"):
            return record.get("member") == member.identity and record["sha256"] == digest(member)
        if "source_depth" not in record:
            return False
        sources = record.get("sources", [])
        return bool(sources) and sources[0]["member"] == member.identity and all(
            source.get(s["name"]).identity == s["member"] and digest(source.get(s["name"])) == s["sha256"]
            for s in sources)
    def publish():
        if verify_only:
            return
        for kind, records in catalogues.items():
            write_json(ROOT / ("resources/" + kind + "_catalogue.json"), records)
            atomic_bytes(ROOT / ("resources/scripts/" + kind + "_catalogue.lua"),
                         ("return " + lua_value(records) + "\n").encode("utf-8"))
    for number, member in enumerate(members, 1):
        kind = "image" if member.extension in (".rct", ".rc8") else "audio"
        stats = counts[kind]
        stats["total"] += 1
        try:
            records = catalogues[kind]
            record = records.get(member.name)
            if valid(member, record):
                stats["reused"] += 1
            else:
                if verify_only:
                    raise ValueError("asset missing or stale")
                record = images.convert(member.name) if kind == "image" else audio.prepare(member.name)
                records[member.name] = record
                stats["prepared"] += 1
            stats["bytes"] += record["bytes"]
        except Exception as error:
            failures.append({"name": member.name, "member": member.identity, "error": str(error)})
            print("asset failed:", member.name, str(error), flush=True)
        if number % 50 == 0 or number == len(members):
            if number % 250 == 0 or number == len(members):
                publish()
            print("all assets:", number, "/", len(members),
                  "prepared", sum(s["prepared"] for s in counts.values()),
                  "reused", sum(s["reused"] for s in counts.values()), flush=True)
    publish()
    movies = movie_catalogue()
    report = {
        "status": "failed" if failures else "passed", "timestamp": timestamp(),
        "verify_only": verify_only, "source_identity": read_json(ROOT / "resources/source_manifest.json")["source_identity"],
        "counts": counts, "failures": failures, "seconds": round(time.monotonic() - began, 2),
        "movies": movies, "original_archives_copied": 0, "movie_copies": 0,
        "scope": "all effective RCT/RC8/OGG/WAV members after archive/translation overrides, not only visited scenes",
    }
    write_json(ROOT / "reports/all_assets.json", report)
    assert not failures, "some assets failed; see reports/all_assets.json"
    return report


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("--verify-only", action="store_true")
    run(p.parse_args().verify_only)
