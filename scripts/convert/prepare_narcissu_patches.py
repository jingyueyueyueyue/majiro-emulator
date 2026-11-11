"""Extract local Narcissu data overlays without executing the Windows patch DLLs."""
from __future__ import annotations

import argparse
import hashlib
import io
import json
from pathlib import Path
import re
import struct
import sys

from port_common import ROOT, archive_dir, atomic_bytes, lua_value, timestamp, write_json
from majiro_mjo import Script
from majiro_arc import Archive

TOOLS = ROOT / "references/player/patch_tools"
MAX_FILE = 128 * 1024 * 1024


def sha(data):
    return hashlib.sha256(data).hexdigest()


def tool_modules():
    records = json.loads((TOOLS / "sources.json").read_text(encoding="utf8"))
    for entry in records["sources"]:
        if sha((TOOLS / entry["path"]).read_bytes()) != entry["sha256"]:
            raise ValueError("pinned patch extraction tool changed: " + entry["path"])
    sys.path.insert(0, str(TOOLS))
    from evbunpack.__main__ import pe_external_tree
    from aplib import decompress
    return pe_external_tree, decompress


def boxed_files(path):
    tree, decompress = tool_modules()
    data = path.read_bytes()
    start = data.find(b"EVB\0")
    if start < 0:
        raise ValueError("Enigma filesystem marker missing")
    stream = io.BytesIO(data)
    stream.seek(start)
    nodes = iter(list(tree(stream)))
    main = next(nodes)
    result = {}

    def visit(node, parent="", depth=0):
        if depth > 16 or len(result) > 10000:
            raise ValueError("patch virtual filesystem exceeds bounds")
        name = node["name"]
        if name == "%DEFAULT FOLDER%":
            name = ""
        if name in (".", "..") or any(c in name for c in "/\\:"):
            raise ValueError("unsafe patch filename")
        relative = (parent + "/" + name).strip("/")
        if node["type"] == 3:
            count = node["objects_count"]
            if not 0 <= count <= 10000:
                raise ValueError("patch directory count outside bounds")
            for _ in range(count):
                visit(next(nodes), relative, depth + 1)
            return
        if node["type"] != 2 or not relative or relative.lower() in result:
            raise ValueError("invalid or duplicate patch member")
        offset, stored, original = node["offset"], node["stored_size"], node["original_size"]
        if not 0 <= stored <= MAX_FILE or not 0 <= original <= MAX_FILE or offset + stored > len(data):
            raise ValueError("patch file data outside source")
        raw = data[offset:offset + stored]
        if stored != original:
            block, = struct.unpack_from("<I", raw)
            if not 8 <= block <= stored or (block - 8) % 4:
                raise ValueError("invalid patch compression chunks")
            cursor, decoded = block, bytearray()
            for at in range(8, block, 12):
                size, = struct.unpack_from("<I", raw, at)
                if not 0 < size <= stored - cursor:
                    raise ValueError("patch compressed chunk outside source")
                decoded.extend(decompress(raw[cursor:cursor + size], strict=True,
                                          max_size=original - len(decoded)))
                cursor += size
            if cursor != stored or len(decoded) != original:
                raise ValueError("patch decompression size mismatch")
            raw = bytes(decoded)
        result[relative.lower()] = {
            "name": relative, "data": raw, "sha256": sha(raw),
            "source_offset": offset, "stored_bytes": stored,
        }

    if not 0 < main["objects_count"] <= 10000:
        raise ValueError("patch filesystem has no root")
    for _ in range(main["objects_count"]):
        visit(next(nodes))
    try:
        next(nodes)
        raise ValueError("unconsumed patch directory entries")
    except StopIteration:
        return result


def translation_units(script):
    """DLL 100056b0 / 10005a30: first-byte-selected ldstr and merged text."""
    units, cursor = [], 0
    rows = script.instructions
    while cursor < len(rows):
        row = rows[cursor]
        if row.name == "ldstr":
            size, = struct.unpack_from("<H", script.code, row.offset + 2)
            raw = script.code[row.offset + 4:row.offset + 3 + size]
            if raw and raw[0] > 0x80:
                units.append({"offset": row.offset, "opcode": row.opcode,
                              "expected": row.operands[0], "raw": raw})
        elif row.name == "text":
            unit = {"offset": row.offset, "opcode": row.opcode, "expected": row.operands[0]}
            while cursor + 4 < len(rows):
                proc, ctrl, line, text = rows[cursor + 1:cursor + 5]
                if ((proc.name, ctrl.name, line.name, text.name) != ("proc", "ctrl", "line", "text")
                        or ctrl.operands[0] != "n"):
                    break
                unit["expected"] += "\\n" + text.operands[0]
                cursor += 4
            unit["resume_offset"] = rows[cursor].offset + rows[cursor].size
            unit["raw"] = unit["expected"].encode("cp932")
            units.append(unit)
        cursor += 1
    return units


def mapped_script(name, raw, text_raw):
    script = Script(name, raw)
    units = translation_units(script)
    utf16 = text_raw.startswith(b"\xff\xfe")
    lines = (text_raw[2:].decode("utf-16le").split("\r\n") if utf16
             else text_raw.split(b"\r\n"))
    if lines and lines[-1] in ("", b""):
        lines.pop()
    if len(units) != len(lines):
        raise ValueError("native patch text count differs: " + name)
    entries, encodings, characters = [], {}, {}
    source_positions = {r.offset for r in script.instructions}
    for unit, line in zip(units, lines):
        if utf16:
            value = line
            encoded = value.encode("gbk", errors="replace")
        elif line == unit["raw"]:
            value, encoded = unit["expected"], line
        else:
            value, encoded = line.decode("gbk"), line
        if unit["opcode"] == 0x801 and "\\n" in value:
            raise ValueError("original DLL forbids newline in ldstr replacement")
        encodings[value] = encoded.hex()
        parts = value.split("\\n")
        for part in parts:
            encodings[part] = part.encode("gbk", errors="replace").hex() if utf16 else encoded.hex()
            for char in part:
                characters[char] = char.encode("gbk", errors="replace").hex()
        if value != unit["expected"]:
            entry = {k: v for k, v in unit.items() if k != "raw"}
            entry["value"] = value
            if entry.get("resume_offset") is not None and entry["resume_offset"] not in source_positions:
                raise ValueError("translated text ends outside source instruction boundary")
            entries.append(entry)
    return ({"sha256": script.sha256, "code_size": script.code_size, "entries": entries,
            "source_unit_count": len(units), "text_sha256": sha(text_raw),
            "text_encoding": "UTF-16LE" if utf16 else "original bytes / CP936",
            "native_mapping": "first raw ldstr byte > 0x80; text + proc/ctrl-n/line/text"},
            encodings, characters)


def build_adapter(manifest, destination):
    files = {r["name"].lower(): r for r in manifest["files"]}
    data = {"schema": 1, "game": manifest["game"], "scripts": {},
            "encoded_strings": {}, "encoded_characters": {}, "font_face": "sans-serif",
            "typography": {"family": "sans-serif", "style": 1, "original_weight": 700},
            "source_patch_sha256": manifest["boxed_exe_sha256"],
            "dialogs": {
                "\u7d42\u4e86\u3057\u3066\u3088\u308d\u3057\u3044\u3067\u3059\u304b\uff1f":
                    "\u5373\u5c06\u7ed3\u675f\u6e38\u620f\uff0c\u786e\u8ba4\u5417\uff1f",
                "\u30bf\u30a4\u30c8\u30eb\u306b\u623b\u308a\u307e\u3059\u3002\u3088\u308d\u3057\u3044\u3067\u3059\u304b\uff1f":
                    "\u5373\u5c06\u8fd4\u56de\u6807\u9898\uff0c\u786e\u8ba4\u5417\uff1f",
            }}
    # All supplied main DLLs overwrite CreateWindowExA's window-name argument.
    # Keep that source caption without reproducing any injected Windows code.
    import pefile
    main_dll = files["narcissu_cn.dll"]
    dll_bytes = Path(main_dll["path"]).read_bytes()
    pe = pefile.PE(data=dll_bytes)
    captions = []
    for section in pe.sections:
        if not section.Characteristics & 0x20000000:
            continue
        for match in re.finditer(rb"\xc7\x45\x10(.{4})\x5d\xff\x25", section.get_data(), re.S):
            address, = struct.unpack("<I", match.group(1))
            captions.append(pe.get_data(address - pe.OPTIONAL_HEADER.ImageBase, 256)
                            .split(b"\0")[0].decode("gbk"))
    if len(captions) != 1:
        raise ValueError("Narcissu DLL window caption hook not uniquely identified")
    data["window_title"] = captions[0]
    deployment, archives = [], []
    for key, record in files.items():
        if key.startswith("#mjo/"):
            name = Path(record["name"]).name
            text = files["#txt/" + Path(name).stem.lower() + ".txt"]
            mapped, strings, chars = mapped_script(name, Path(record["path"]).read_bytes(),
                                                   Path(text["path"]).read_bytes())
            data["scripts"][name.lower()] = mapped
            data["encoded_strings"].update(strings)
            data["encoded_characters"].update(chars)
            # The original DLL rebuilds from #mjo, overriding the version in
            # update.arc. Deploy those same bytes as the player's loose layer.
            deployment.append({"source": record["path"], "target": name, "sha256": record["sha256"],
                               "purpose": "original MJO consumed by the verified text adapter"})
        elif key.endswith(".arc"):
            archive = Archive(Path(record["path"]))
            archives.append({"name": record["name"], "version": archive.version,
                             "members": len(archive.members),
                             "extensions": {extension: sum(m.extension == extension for m in archive.members)
                                            for extension in sorted({m.extension for m in archive.members})}})
            deployment.append({"source": record["path"], "target": record["name"],
                               "sha256": record["sha256"], "purpose": "original Chinese resource overlay"})
    identity = sha(json.dumps(data, ensure_ascii=True, sort_keys=True, separators=(",", ":")).encode("ascii"))
    data["identity"] = identity
    adapter = ("-- Verified original Narcissu patch data; no compiled MJO instructions.\nreturn " +
               lua_value(data) + "\n").encode("utf8")
    target = destination / "adapter.lua"
    atomic_bytes(target, adapter)
    deployment.append({"source": str(target), "target": "patches/text_adapter.lua",
                       "sha256": sha(adapter), "purpose": "data-only original text mapping"})
    manifest.update(runtime_status="original_script_text_adapter_ready",
                    adapter_identity=identity, adapter_sha256=sha(adapter),
                    scripts=len(data["scripts"]),
                    text_units=sum(r["source_unit_count"] for r in data["scripts"].values()),
                    replacements=sum(len(r["entries"]) for r in data["scripts"].values()),
                    archives=archives, deployment=deployment,
                    original_dll_evidence="10004f10 / 100056b0 / 10005a30 / 10005d10 / 10005f90",
                    byte_semantics="exact known patch strings; original bytes retained for unchanged names",
                    font_substitution="original YouYuan/GB2312/weight700 uses Android CJK fallback with BOLD",
                    window_title=data["window_title"], source_save_directory="save_chs",
                    save_policy="separate portable patch identity; original save_chs remains read-only")


def prepare(game, evidence):
    root = Path(game["directory"])
    candidates = []
    for source in root.glob("*.exe"):
        if "_cn" in source.stem.lower() or "_chs" in source.stem.lower():
            if b"EVB\0" in source.read_bytes():
                candidates.append(source)
    if len(candidates) != 1:
        raise ValueError("expected exactly one boxed Chinese patch for " + game["id"])
    source = candidates[0]
    source_digest = sha(source.read_bytes())
    files = boxed_files(source)
    destination = ROOT / "localization/narcissu" / game["id"]
    records = []
    for key, entry in files.items():
        raw = entry["data"]
        original = root / entry["name"]
        record = {k: v for k, v in entry.items() if k != "data"}
        record["bytes"] = len(raw)
        record["boxed_exe_sha256"] = source_digest
        if original.is_file() and sha(original.read_bytes()) == entry["sha256"]:
            record["path"] = str(original)
            record["storage"] = "existing_local_source"
        elif key.endswith((".dll", ".exe")):
            target = evidence / (game["id"] + "_" + Path(entry["name"]).name)
            target.write_bytes(raw)
            record["path"] = str(target)
            record["storage"] = "analysis_only_never_executed"
        else:
            target = destination / "source" / entry["name"]
            target.parent.mkdir(parents=True, exist_ok=True)
            atomic_bytes(target, raw)
            record["path"] = str(target)
            record["storage"] = "verified_raw_patch_data"
        records.append(record)
    manifest = {
        "schema": 1, "format": "Majiro Narcissu original patch adapter",
        "game": game["id"], "timestamp": timestamp(), "source_directory": str(root),
        "boxed_exe": str(source), "boxed_exe_sha256": source_digest,
        "files": records, "windows_code_executed": False,
        "source_text_rewritten": False, "runtime_status": "extracted_pending_text_mapping",
    }
    destination.mkdir(parents=True, exist_ok=True)
    build_adapter(manifest, destination)
    write_json(destination / "manifest.json", manifest)
    return manifest


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--game", choices=("all", "narci_himeko", "narci_iris", "narci_onetwo",
                                          "narci_sumire", "narci_zero"), default="all")
    args = parser.parse_args()
    evidence = archive_dir("narcissu_patch_data")
    config = json.loads((ROOT / "config/player_samples.json").read_text(encoding="utf8"))
    games = [g for g in config["games"] if g["id"].startswith("narci_")
             and args.game in ("all", g["id"])]
    reports = [prepare(game, evidence) for game in games]
    report = {"schema": 1, "timestamp": timestamp(), "games": reports,
              "evidence": str(evidence.relative_to(ROOT)), "device_accessed": False}
    write_json(ROOT / "reports/narcissu_patches.json", report)
    write_json(evidence / "20261110_narcissu_patches.json", report)
    print("Verified data-only boxed patches:", len(reports),
          "members:", sum(len(g["files"]) for g in reports))


if __name__ == "__main__":
    main()
