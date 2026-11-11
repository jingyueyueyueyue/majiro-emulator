"""Inventory supplied game DLLs, loader links and embedded archive members.

Never executes DLLs or installers. Generated player/Artemis binaries and old
analysis evidence are excluded from the current game's original-source scan.
"""
from __future__ import annotations
from collections import Counter
import hashlib
import json
from pathlib import Path
import re
import sys

import capstone
import pefile

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "convert"))
from port_common import ROOT, archive_dir, timestamp, write_json
from majiro_arc import Archive
from majiro_mjo import Script

EXCLUDED = {"archive", "resources", "references", "runtime", "player", "projects",
            ".git", ".workbuddy", "localization", "scripts", "reports"}
LOAD_APIS = {"LoadLibraryA", "LoadLibraryW", "LoadLibraryExA", "LoadLibraryExW",
             "GetProcAddress", "FreeLibrary"}
SYSTEM_MEDIA_APIS = {
    "DirectSoundCreate", "DirectSoundCreate8", "DirectDrawCreate", "DirectDrawCreateEx",
    "Direct3DCreate9", "DirectInput8Create", "CoCreateInstance", "mciSendStringA",
    "AVIFileOpenA", "waveOutOpen", "GetTextExtentPoint32A", "TextOutA"}


def strings(raw):
    values = [(m.start(), m.group().decode("ascii")) for m in re.finditer(rb"[\x20-\x7e]{4,}", raw)]
    values += [(m.start(), m.group().decode("utf-16le")) for m in
               re.finditer(rb"(?:[\x20-\x7e]\0){4,}", raw)]
    return values


def version_info(pe):
    result = []
    for group in getattr(pe, "FileInfo", []):
        for info in group:
            for table in getattr(info, "StringTable", []):
                values = {k.decode("utf8", "replace"): v.decode("utf8", "replace")
                          for k, v in table.entries.items()}
                if values not in result:
                    result.append(values)
    return result


def inspect_pe(path):
    raw = path.read_bytes()
    row = {"path": str(path), "bytes": len(raw), "sha256": hashlib.sha256(raw).hexdigest()}
    try:
        pe = pefile.PE(data=raw)
    except pefile.PEFormatError as error:
        row.update(status="invalid_pe", error=str(error))
        return row
    imports = {}
    addresses = {}
    for library in getattr(pe, "DIRECTORY_ENTRY_IMPORT", []):
        name = library.dll.decode("ascii", "replace")
        imports[name] = []
        for entry in library.imports:
            symbol = entry.name.decode("ascii", "replace") if entry.name else f"ordinal_{entry.ordinal}"
            imports[name].append(symbol)
            addresses[entry.address] = name + "!" + symbol
    for library in getattr(pe, "DIRECTORY_ENTRY_DELAY_IMPORT", []):
        name = library.dll.decode("ascii", "replace")
        imports.setdefault(name, [])
        imports[name] += [entry.name.decode("ascii", "replace") if entry.name
                          else f"ordinal_{entry.ordinal}" for entry in library.imports]
    exports = [{"name": entry.name.decode("ascii", "replace") if entry.name else None,
                "ordinal": entry.ordinal, "rva": entry.address,
                "forwarder": entry.forwarder.decode("ascii", "replace") if entry.forwarder else None}
               for entry in getattr(getattr(pe, "DIRECTORY_ENTRY_EXPORT", None), "symbols", [])]
    values = strings(raw)
    dll_strings = []
    for offset, text in values:
        for match in re.finditer(r"(?i)(?:[A-Za-z0-9_. -]+[\\/])*[A-Za-z0-9_.-]+\.dll\b", text):
            value = match.group().strip()
            if not any(r["value"].casefold() == value.casefold() for r in dll_strings):
                dll_strings.append({"value": value, "file_offset": offset + match.start()})
    links = []
    md = capstone.Cs(capstone.CS_ARCH_X86, capstone.CS_MODE_32)
    md.detail = True
    for section in pe.sections:
        if not section.Characteristics & 0x20000000:
            continue
        code = section.get_data()
        for match in re.finditer(rb"\xff\x15(.{4})", code, re.DOTALL):
            at = pe.OPTIONAL_HEADER.ImageBase + section.VirtualAddress + match.start()
            rows = list(md.disasm(code[match.start():match.start() + 6], at))
            if not rows or rows[0].mnemonic != "call":
                continue
            symbol = addresses.get(rows[0].operands[0].mem.disp, "")
            if symbol.split("!")[-1] in LOAD_APIS | SYSTEM_MEDIA_APIS:
                links.append({"va": at, "import": symbol})
    row.update(status="parsed", machine=f"{pe.FILE_HEADER.Machine:04x}",
               imports=imports, exports=exports, version_info=version_info(pe),
               dll_strings=dll_strings, loader_and_media_call_sites=links)
    return row


def classify(row):
    names = {e["name"] for e in row.get("exports", [])}
    metadata = row.get("version_info", [])
    microsoft = any(v.get("CompanyName") == "Microsoft Corporation" for v in metadata)
    description = " ".join(v.get("FileDescription", "") for v in metadata)
    if microsoft and ("DirectXSetupA" in names or "iDirectXSetup" in names):
        return {
            "kind": "windows_directx_installer",
            "reason": "Microsoft version resource plus DirectX installer exports",
            "semantics": "Installs/registers Windows DirectX components; this is a setup prerequisite, not a Majiro syscall extension.",
            "android_action": "Use existing Android SDL/audio/video host; do not load or emulate Windows DirectX setup.",
            "android_plugin_implementation_required": False,
            "description": description,
        }
    if any(name in ("Narcissu_cn.dll", "FontPatch.dll") for name in (Path(row["path"]).name,)):
        return {"kind": "translation_hook", "owner": "native_save",
                "android_action": "See reports/narcissu_patch_contract.md; original Windows hook is reimplemented by adapter data.",
                "android_plugin_implementation_required": True}
    if any((e or "").startswith(("BASS_", "FMOD_", "avcodec_", "vorbis_", "ogg_", "SDL_")) for e in names):
        return {"kind": "media_runtime", "android_action": "Recover codec/channel boundary before choosing a replacement.",
                "android_plugin_implementation_required": None}
    return {"kind": "unclassified_plugin_candidate", "android_action": "Recover exports and actual caller before compatibility claim.",
            "android_plugin_implementation_required": None}


def main():
    games = []
    for name in ("player_samples.json", "player_new_samples.json"):
        games += json.loads((ROOT / "config" / name).read_text(encoding="utf8"))["games"]
    unique = {}
    for game in games:
        unique[str(Path(game["directory"]).resolve())] = game
    report = {"schema": 1, "timestamp": timestamp(), "sample_count": len(unique), "samples": [],
              "dlls_executed": False, "game_files_modified": False, "android_device_accessed": False,
              "scope": "all original game-directory DLL files, archived DLL members, game EXE imports and script DLL literals",
              "workspace_excluded_directories": sorted(EXCLUDED)}
    for game in unique.values():
        root = Path(game["directory"])
        binaries = []
        for path in root.rglob("*"):
            if not path.is_file() or path.suffix.casefold() not in (".dll", ".exe"):
                continue
            relative = path.relative_to(root)
            if root.resolve() == ROOT.resolve() and relative.parts[0].casefold() in EXCLUDED:
                continue
            binaries.append(path)
        dlls = []
        executables = []
        for path in sorted(binaries):
            row = inspect_pe(path)
            row["relative_path"] = str(path.relative_to(root))
            if path.suffix.casefold() == ".dll":
                row["classification"] = classify(row)
                dlls.append(row)
            else:
                executables.append(row)
        packed = []
        script_literals = []
        for path in sorted(root.glob("*.arc")):
            if path.name.casefold() == "savedata.arc":
                continue
            arc = Archive(path)
            for member in arc.members:
                if member.extension == ".dll":
                    packed.append({"archive": path.name, "name": member.name,
                                   "bytes": member.size, "sha256": member.digest()})
                elif member.extension == ".mjo":
                    script = Script(member.name, member.read())
                    for instruction in script.instructions:
                        for value in instruction.operands:
                            if isinstance(value, str) and re.search(r"(?i)\.dll\b", value):
                                script_literals.append({"archive": path.name, "script": member.name,
                                                        "offset": instruction.offset, "value": value})
        dependency_links = []
        for dll in dlls:
            basename = Path(dll["path"]).name.casefold()
            for exe in executables:
                static = [n for n in exe.get("imports", {}) if Path(n).name.casefold() == basename]
                dynamic = [n for n in exe.get("dll_strings", []) if Path(n["value"]).name.casefold() == basename]
                if static or dynamic:
                    dependency_links.append({"dll": dll["relative_path"], "executable": exe["relative_path"],
                                             "static_import": bool(static), "dynamic_name_candidates": dynamic})
        sample = {"sample": game["id"], "directory": str(root), "dll_files": dlls,
                  "archive_dll_members": packed, "executable_imports": executables,
                  "script_dll_literals": script_literals, "local_dependency_links": dependency_links}
        report["samples"].append(sample)
        print(game["id"], "DLL", len(dlls), "packed DLL", len(packed),
              "script DLL literals", len(script_literals), flush=True)
    report["dll_file_count"] = sum(len(s["dll_files"]) for s in report["samples"])
    report["unique_dll_sha256"] = sorted({r["sha256"] for s in report["samples"] for r in s["dll_files"]})
    report["classifications"] = dict(Counter(r["classification"]["kind"] for s in report["samples"] for r in s["dll_files"]))
    write_json(ROOT / "reports/game_dll_inventory.json", report)
    lines = ["# Game DLL inventory", "", "Date: 2026-11-10. Read-only inspection; no DLL or game was executed.",
             "", "| Sample | DLL files | Packed DLL members | Script DLL literals |",
             "| --- | ---: | ---: | ---: |"]
    lines += [f'| {s["sample"]} | {len(s["dll_files"])} | {len(s["archive_dll_members"])} | {len(s["script_dll_literals"])} |'
              for s in report["samples"]]
    lines += ["", f'Total: {report["dll_file_count"]} files, {len(report["unique_dll_sha256"])} distinct SHA-256 fingerprints.',
              "", "## Non-Narcissu behavior", ""]
    for sample in report["samples"]:
        if sample["sample"].startswith("narci_"):
            continue
        for row in sample["dll_files"]:
            kind = row["classification"]
            lines += [f'- {sample["sample"]}/{row["relative_path"]}: **{kind["kind"]}**, '
                      f'`{row["sha256"]}`. {kind.get("semantics", "")} {kind["android_action"]}']
    lines += ["", "Narcissu translation DLL semantics and Android replacement are owned by the native-save/patch work; see `reports/narcissu_patch_contract.md`.",
              "", "DirectX setup exports perform Windows installation and driver registration. They are not dynamically registered Majiro script interfaces. "
              "The inventory records exact imports, exports, version resources and EXE name references; this conclusion does not rely on a filename alone.",
              "", "## Limits", "", "Import/name references are caller candidates, not full control-flow proof. Unknown plugin classifications remain explicit. "
              "No Android compatibility is inferred solely from a static DLL inventory."]
    (ROOT / "reports/game_dll_inventory.md").write_text("\n".join(lines) + "\n", encoding="utf8")
    folder = archive_dir("game_dll_inventory")
    write_json(folder / "20261110_game_dll_inventory.json", report)
    print("DLL files", report["dll_file_count"], "classification", report["classifications"])


if __name__ == "__main__":
    main()
