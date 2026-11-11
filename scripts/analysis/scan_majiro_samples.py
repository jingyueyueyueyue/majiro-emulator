"""Inventory original game directories without extracting or executing games."""
from __future__ import annotations
import argparse
from collections import Counter
import hashlib
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "convert"))
from majiro_arc import Archive
from majiro_config import environment_values
from majiro_mjo import Script, opcode_table
from port_common import ROOT, timestamp, write_json

EXCLUDED = {"archive", "savedata", "save_chs", "node_modules", ".git"}


def discover(root: Path, depth=2):
    root = root.resolve()
    if not root.is_dir():
        raise FileNotFoundError(root)
    found = []
    if any(p.name.lower() != "savedata.arc" for p in root.glob("*.arc")):
        found.append(root)
    if depth:
        for child in sorted(root.iterdir()):
            if child.is_dir() and not child.is_symlink() and child.name.lower() not in EXCLUDED:
                found.extend(discover(child, depth - 1))
    return found


def scan(root: Path, definitions=None, include_exe=True):
    definitions = definitions or opcode_table()
    result = {
        "directory": str(root.resolve()), "archives": [], "scripts": [],
        "diagnostics": [], "runtime_verified": False, "playable": None,
        "environments": [],
    }
    extensions = Counter()
    signatures = Counter()
    opcodes = Counter()
    calls = Counter()
    image_variants = Counter()
    for path in sorted(root.glob("*.arc")):
        if path.name.lower() == "savedata.arc":
            continue
        try:
            archive = Archive(path)
        except (ValueError, UnicodeError, OSError) as error:
            result["diagnostics"].append({"file": path.name, "error": str(error)})
            continue
        result["archives"].append({
            "name": path.name, "version": archive.version, "bytes": archive.size,
            "members": len(archive.members), "index_sha256": archive.index_sha256,
        })
        with path.open("rb") as stream:
            for member in archive.members:
                if member.extension in (".rct", ".rc8"):
                    stream.seek(member.offset)
                    header = stream.read(min(member.size, 8))
                    variant = header[4:8].decode("ascii", "replace")
                    image_variants[variant] += 1
        for member in archive.members:
            extensions[member.extension] += 1
            if member.extension == ".env":
                try:
                    env = environment_values(member.read().decode("cp932"))
                    result["environments"].append({
                        "archive": path.name, "member": member.name,
                        "width": env.get("CLIENT_WIDTH"), "height": env.get("CLIENT_HEIGHT"),
                        "font_width": env.get("FC_WIDTH"), "font_height": env.get("FC_HEIGHT"),
                    })
                except (UnicodeError, ValueError) as error:
                    result["diagnostics"].append({"file": member.identity, "error": str(error)})
            if member.extension != ".mjo":
                continue
            data = member.read()
            signatures[data[:16].decode("ascii", "replace").rstrip("\0")] += 1
            row = {"archive": path.name, "member": member.name,
                   "sha256": hashlib.sha256(data).hexdigest()}
            try:
                parsed = Script(member.name, data, opcodes=definitions)
                row.update(instructions=len(parsed.instructions), status="parsed", has_main=parsed.has_main)
                for ins in parsed.instructions:
                    opcodes[ins.name] += 1
                    if ins.name in ("syscall", "syscallp"):
                        hash_value = ins.operands[ins.encoding.index("h")]
                        calls[f"{hash_value:08x}"] += 1
            except (ValueError, UnicodeError) as error:
                # Do not replace invalid bytes or repair branches silently.
                row.update(status="failed", error=str(error))
            result["scripts"].append(row)
    result.update(extensions=dict(extensions), script_signatures=dict(signatures),
                  opcodes=dict(sorted(opcodes.items())), syscalls=dict(sorted(calls.items())),
                  image_variants=dict(sorted(image_variants.items())),
                  archive_versions=sorted({a["version"] for a in result["archives"]}))
    result["parsed_scripts"] = sum(s["status"] == "parsed" for s in result["scripts"])
    result["failed_scripts"] = len(result["scripts"]) - result["parsed_scripts"]
    result["scripts_without_main"] = [
        {"archive": s["archive"], "member": s["member"]} for s in result["scripts"]
        if s.get("has_main") is False]
    result["structural_status"] = (
        "failed" if result["diagnostics"] or result["failed_scripts"] else
        "passed" if result["archives"] else "no_majiro_archives")
    result["loose_script_directories"] = {
        name: len(list((root / name).glob("*.mjo")))
        for name in ("scenario", "#mjo") if (root / name).is_dir()
    }
    result["movies"] = [
        {"name": p.name, "bytes": p.stat().st_size} for p in sorted((root / "movie").glob("*"))
        if p.is_file()]
    result["save_signatures"] = []
    for dirname in ("savedata", "save_chs"):
        directory = root / dirname
        if not directory.is_dir():
            continue
        for path in sorted(directory.glob("*")):
            if not path.is_file() or path.suffix.lower() not in (".mss", ".sav"):
                continue
            with path.open("rb") as stream:
                header = stream.read(16)
            result["save_signatures"].append({
                "path": f"{dirname}/{path.name}", "bytes": path.stat().st_size,
                "header_hex": header.hex(),
                "signature": header.rstrip(b"\0").decode("ascii", "replace"),
                "import_verified": False,
            })
    if include_exe:
        from inspect_majiro_exe import inspect_executable, embedded_engines
        names = {int(k, 16): v for k, v in json.loads(
            (ROOT / "references/majiro-py/known_hashes.json").read_text(encoding="utf-8"))["syscalls"].items()}
        result["executables"] = []
        for parent in (root, root / "bin"):
            if not parent.is_dir():
                continue
            for path in sorted(parent.glob("*.exe")):
                if path.name.lower() in {"uninst.exe", "install.exe", "autorun.exe"}:
                    continue
                inspected = inspect_executable(path, names)
                if inspected["signatures"] or inspected["boxed_patch_candidate"]:
                    result["executables"].append(inspected)
                embedded = embedded_engines(path)
                if embedded:
                    inspected["embedded_engine_container"] = True
                    for payload in embedded:
                        folder = ROOT / "archive/2026-11" / ("20261110_embedded_engine_" + payload["sha256"][:12])
                        folder.mkdir(parents=True, exist_ok=True)
                        archived = folder / "20261110_engine.exe"
                        if not archived.exists():
                            archived.write_bytes(payload["data"])
                        elif hashlib.sha256(archived.read_bytes()).hexdigest() != payload["sha256"]:
                            raise ValueError("archived embedded engine fingerprint changed")
                        child = inspect_executable(archived, names)
                        child.update(container_path=str(path), container_sha256=payload["container_sha256"],
                                     embedded_offset=payload["offset"], embedded_size=payload["size"],
                                     provenance="complete PE payload embedded in supplied executable")
                        result["executables"].append(child)
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("directories", type=Path, nargs="*")
    parser.add_argument("--samples", type=Path, help="JSON manifest containing a games list of directories")
    parser.add_argument("--depth", type=int, default=1, choices=range(4))
    parser.add_argument("--skip-exe", action="store_true")
    parser.add_argument("--output", type=Path, default=ROOT / "reports/majiro_samples.json")
    args = parser.parse_args()
    if args.samples:
        manifest = json.loads(args.samples.read_text(encoding="utf-8"))
        args.directories.extend(Path(g["directory"]) for g in manifest["games"])
    if not args.directories:
        parser.error("provide a game directory or --samples manifest")
    roots = sorted({p for root in args.directories for p in discover(root, args.depth)})
    if not roots:
        parser.error("no game directories containing ARC files found")
    definitions = opcode_table()
    games = []
    for root in roots:
        game = scan(root, definitions, not args.skip_exe)
        games.append(game)
        print(f"{root.name}: ARC {game['archive_versions']}, "
              f"MJO {game['parsed_scripts']}/{len(game['scripts'])}, "
              f"{game['structural_status']}", flush=True)
    write_json(args.output, {
        "schema": 1, "timestamp": timestamp(), "games": games,
        "scope": "static structure and PE inspection; no gameplay compatibility claim",
        "sources_modified": False,
    })
    print("Report:", args.output)
    return int(any(g["structural_status"] == "failed" for g in games))


if __name__ == "__main__":
    raise SystemExit(main())
