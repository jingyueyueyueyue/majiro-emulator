"""Create one indexed source view and compact Lua scripts, without bulk extraction."""
from __future__ import annotations
from collections import Counter, defaultdict
import hashlib
import json
import os
from pathlib import Path
import sqlite3
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "convert"))
from port_common import ROOT, atomic_bytes, config, lua_value, read_json, sha256, write_json
from majiro_arc import Catalogue
from majiro_mjo import Script, opcode_table
from proofread_text import Overrides, OVERRIDE_PATH


def prepare():
    catalogue = Catalogue(ROOT)
    manifest_path = ROOT / "resources/source_manifest.json"
    previous_manifest = read_json(manifest_path) if manifest_path.exists() else {}
    lock_path = ROOT / "config/source.lock.json"
    locked = read_json(lock_path) if lock_path.exists() else None
    if locked:
        # Validate before touching generated scripts, so a changed source
        # cannot partially replace a previously consistent development build.
        for record in [*locked["archives"], *locked["files"]]:
            path = ROOT / record["path"]
            if not path.exists() or sha256(path) != record["sha256"]:
                raise ValueError("frozen original source changed: " + record["path"])
    opcodes = opcode_table()
    reviewed = Overrides() if catalogue.patch else None
    known = read_json(ROOT / "references/majiro-py/known_hashes.json")
    syscall_names = {int(k, 16): v for k, v in known["syscalls"].items()}
    calls = {}
    controls = Counter()
    instructions = Counter()
    scripts, runtime_catalogue, source_records, outputs = [], {}, [], []
    config_files = {}
    config_lines = {}
    from majiro_config import environment_values
    environment = environment_values(catalogue.get("majiro.env").read().decode("cp932"))
    raw_bytes = sum(arc.size for arc in catalogue.archives)
    image_variants = Counter()
    image_metadata = {}
    extension_counts = Counter()
    members = []
    for archive in catalogue.archives:
        source_records.append({"path": archive.path.name, "bytes": archive.size,
                               "sha256": sha256(archive.path), "index_sha256": archive.index_sha256,
                               "members": len(archive.members)})
    for member in catalogue.all_members():
        extension_counts[member.extension] += 1
        effective=catalogue.get(member.name).identity == member.identity
        members.append((member.identity, member.archive.name, member.index, member.name,
                        member.raw_name, "%016x" % member.name_hash, member.offset, member.size,int(effective)))
        if member.extension in (".rct", ".rc8"):
            with member.archive.open("rb") as stream:
                import struct
                stream.seek(member.offset)
                head = stream.read(20)
                image_variants[head[4:8].decode("ascii")] += 1
                if effective:
                    width, height = struct.unpack_from("<II", head, 8)
                    image_metadata[member.name.lower()] = {
                        "width": width, "height": height, "name": member.name,
                        "depth": 8 if member.extension == ".rc8" else 32
                    }
        if not effective:continue
        localized=member.identity.startswith("translation:")
        decode=catalogue.patch.decode if localized else lambda b:b.decode("cp932")
        if member.extension == ".cfg":
            from majiro_config import config_tokens, line_remainders
            text = decode(member.read())
            if reviewed:
                text = reviewed.apply_config(member.name, hashlib.sha256(member.read()).hexdigest(), text)
            config_files[member.name] = config_tokens(text)
            config_lines[member.name] = line_remainders(text)
        if member.extension != ".mjo":
            continue
        raw=member.read()
        repairs=catalogue.patch.repairs(member.name,member.sha256)if localized else None
        preludes=catalogue.patch.preludes(member.name,member.sha256)if localized else None
        script = Script(member.name,raw,opcodes,string_decoder=decode,address_repairs=repairs,stack_repairs=preludes)
        runtime_data = script.runtime_data()
        if reviewed:
            reviewed.apply_script(script, runtime_data)
        scripts.append(script.summary())
        runtime_catalogue[script.name] = {"id": script.id, "sha256": script.sha256}
        for ins in script.instructions:
            instructions[ins.name] += 1
            if ins.name == "ctrl":
                controls[ins.operands[0]] += 1
            if ins.name in ("syscall", "syscallp"):
                h, count = ins.operands
                key = "%08x/%d/%s" % (h, count, ins.name)
                record = calls.setdefault(key, {
                    "hash": "%08x" % h, "name": syscall_names.get(h), "argc": count,
                    "opcode": ins.name, "calls": 0, "examples": [],
                    "semantic_status": "unverified", "runtime_status": "not_implemented",
                })
                record["calls"] += 1
                if len(record["examples"]) < 3:
                    record["examples"].append({"script": script.name, "offset": ins.offset})
        path = ROOT / "resources/scripts" / (script.id + ".lua")
        if localized:
            formal=ROOT/"localization/scripts"/path.name
            atomic_bytes(formal,("return "+lua_value(runtime_data)+"\n").encode("utf-8"))
            path.parent.mkdir(parents=True,exist_ok=True)
            if not path.exists()or not os.path.samefile(path,formal):
                temporary=path.with_name(path.name+".link-tmp")
                try:
                    os.link(formal,temporary);os.replace(temporary,path)
                finally:
                    if temporary.exists():temporary.unlink()
            outputs.append({"path":formal.relative_to(ROOT).as_posix(),"sha256":sha256(formal),
                            "bytes":formal.stat().st_size})
        else:
            atomic_bytes(path, ("return " + lua_value(runtime_data) + "\n").encode("utf-8"))
        outputs.append({"path": path.relative_to(ROOT).as_posix(), "sha256": sha256(path),
                        "bytes": path.stat().st_size})
    root = ROOT / "resources"
    if reviewed:
        reviewed.finish()
    root.mkdir(exist_ok=True)
    # Build a bounded indexed catalogue in memory, then compare before replacing.
    connection = sqlite3.connect(":memory:")
    connection.execute("CREATE TABLE members(identity TEXT PRIMARY KEY, archive TEXT, member_index INTEGER,"
                       "name TEXT, raw_name BLOB, name_hash TEXT, offset INTEGER, size INTEGER, effective INTEGER)")
    connection.executemany("INSERT INTO members VALUES(?,?,?,?,?,?,?,?,?)", members)
    connection.execute("CREATE INDEX member_names ON members(name, effective)")
    connection.commit()
    atomic_bytes(root / "source_index.sqlite3", connection.serialize())
    connection.close()
    duplicates = [
        {"name": name, "candidates": [m.identity for m in items], "selected": items[0].identity}
        for name, items in catalogue.by_name.items() if len(items) > 1
    ]
    for name, data in (
        ("catalogue.lua", {"schema": 1, "scripts": runtime_catalogue, "entry": config()["entry_script"]}),
        ("opcodes.lua", {k: [name, encoding] for k, (name, encoding) in opcodes.items()}),
        ("syscall_names.lua", syscall_names),
        ("config_files.lua", config_files),
        ("image_metadata.lua", image_metadata),
        ("environment.lua", environment),
        ("config_lines.lua", config_lines),
        ("resource_names.lua", {name.lower(): True for name in catalogue.by_name}),
    ):
        path = root / "scripts" / name
        atomic_bytes(path, ("return " + lua_value(data) + "\n").encode("utf-8"))
        outputs.append({"path": path.relative_to(ROOT).as_posix(), "sha256": sha256(path),
                        "bytes": path.stat().st_size})
    from majiro_patch import tunnel_table
    display_map=reviewed.characters if reviewed else {}
    translation={"characters":display_map,"tunnel":reviewed.tunnel if reviewed else {},
                 "labels":read_json(ROOT/"localization/ui_display.json")if(ROOT/"localization/ui_display.json").exists()else {}}
    path=root/"scripts/translation.lua"
    atomic_bytes(path,("return "+lua_value(translation)+"\n").encode("utf-8"))
    outputs.append({"path":path.relative_to(ROOT).as_posix(),"sha256":sha256(path),"bytes":path.stat().st_size})
    source_files = []
    for path in sorted((ROOT / "bin").glob("*.exe")):
        if path.name != "uninst.exe":
            source_files.append({"path": path.relative_to(ROOT).as_posix(),
                                 "sha256": sha256(path), "bytes": path.stat().st_size})
    for path in sorted((ROOT / "movie").glob("*")):
        if path.is_file():
            source_files.append({"path": path.relative_to(ROOT).as_posix(),
                                 "sha256": sha256(path), "bytes": path.stat().st_size})
    source_identity = hashlib.sha256(json.dumps(source_records + source_files, sort_keys=True).encode()).hexdigest()
    source_lock = {"schema": 1, "source_identity": source_identity,
                   "archives": source_records, "files": source_files}
    if locked and locked != source_lock:
        raise ValueError("original source changed; baseline must be explicitly reviewed")
    if not locked:
        write_json(lock_path, source_lock)
    original_identity=source_identity
    if catalogue.patch:
        source_identity=hashlib.sha256((original_identity+sha256(ROOT/"localization/patch.lock.json")+
                                       sha256(ROOT/"localization/relocations.json")+
                                       sha256(ROOT/"localization/stack_repairs.json")+
                                       sha256(ROOT/"localization/character_map.json")).encode()).hexdigest()
    # Text-only proofreading changes the build identity, not the bytecode
    # positions or source-script hashes used by existing saves.
    save_identity=source_identity
    if catalogue.patch:
        if reviewed.entries:
            source_identity=hashlib.sha256((source_identity+sha256(OVERRIDE_PATH)).encode()).hexdigest()
    compatible_saves={source_identity}
    if previous_manifest.get("save_identity")==save_identity:
        compatible_saves.update(previous_manifest.get("save_compatible_identities",[]))
        compatible_saves.add(previous_manifest["source_identity"])
    manifest = {"schema": 2, "source_identity": source_identity,"original_source_identity":original_identity,
                "save_identity":save_identity,
                "save_compatible_identities":sorted(compatible_saves),
                "translation":catalogue.patch.lock if catalogue.patch else None,"archives": source_records,
                "files": source_files, "scripts": scripts, "outputs": outputs,
                "proofread_sha256":sha256(OVERRIDE_PATH)if OVERRIDE_PATH.exists()else None}
    write_json(ROOT / "resources/source_manifest.json", manifest)
    hashes = {r["hash"] for r in calls.values()}
    report = {
        "schema": 1, "source_identity": source_identity,
        "source_archive_bytes": raw_bytes, "members": len(members),
        "extensions": dict(extension_counts), "image_variants": dict(image_variants),
        "script_count": len(scripts), "instructions": sum(instructions.values()),
        "opcode_variants": len(instructions), "opcodes": dict(instructions),
        "syscall_hashes": len(hashes),
        "syscall_hash_argcount_pairs": len({(r["hash"], r["argc"]) for r in calls.values()}),
        "syscall_signatures_including_return": len(calls),
        "unresolved_hashes": sorted({r["hash"] for r in calls.values() if r["name"] is None}),
        "control_tokens": dict(controls), "duplicate_names": duplicates,
        "archive_order_status": config()["archive_order_evidence"],
        "structural_status": "passed", "runtime_status": "incomplete", "release_allowed": False,
    }
    write_json(ROOT / "reports/source_audit.json", report)
    write_json(ROOT / "reports/syscall_inventory.json", {
        "source_identity": source_identity, "calls": sorted(calls.values(), key=lambda row: row["hash"])
    })
    return report


def verify():
    manifest = read_json(ROOT / "resources/source_manifest.json")
    locked = read_json(ROOT / "config/source.lock.json")
    if manifest.get("original_source_identity",manifest["source_identity"]) != locked["source_identity"]:
        raise ValueError("source manifest no longer matches frozen baseline")
    for record in [*manifest["archives"], *manifest["files"], *manifest["outputs"]]:
        path = ROOT / record["path"]
        if not path.is_file() or sha256(path) != record["sha256"]:
            raise ValueError(f"source/generated member mismatch: {path}")
    if manifest.get("translation"):
        translation=manifest["translation"]
        if (ROOT/"config/translation_source.json").exists():
            from majiro_patch import Patch
            patch=Patch()
            if patch.lock!=translation:
                raise ValueError("translation member lock changed")
            for member in patch.members:
                member.read()
        elif sha256(Path(translation["zip"]))!=translation["sha256"]:
            raise ValueError("translation ZIP changed")
        expected=hashlib.sha256((locked["source_identity"]+sha256(ROOT/"localization/patch.lock.json")+
                                sha256(ROOT/"localization/relocations.json")+
                                sha256(ROOT/"localization/stack_repairs.json")+
                                sha256(ROOT/"localization/character_map.json")).encode()).hexdigest()
        if manifest.get("save_identity",expected)!=expected:
            raise ValueError("save compatibility sources changed")
        if OVERRIDE_PATH.exists() and read_json(OVERRIDE_PATH)["entries"]:
            expected=hashlib.sha256((expected+sha256(OVERRIDE_PATH)).encode()).hexdigest()
        if expected!=manifest["source_identity"]:raise ValueError("effective translation sources changed")
    current_proofread=sha256(OVERRIDE_PATH)if OVERRIDE_PATH.exists()else None
    if current_proofread!=manifest.get("proofread_sha256"):
        raise ValueError("formal proofreading overrides changed")
    return {"status": "passed", "source_identity": manifest["source_identity"],
            "checked_files": len(manifest["archives"]) + len(manifest["files"]) + len(manifest["outputs"])}


if __name__ == "__main__":
    result = verify() if "--verify-only" in sys.argv else prepare()
    print(json.dumps({k: result[k] for k in ("structural_status", "script_count", "instructions",
                                            "opcode_variants", "syscall_hashes", "status") if k in result}))
