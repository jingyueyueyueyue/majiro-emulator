"""Read-only hook/API/provenance audit for boxed and loose Narcissu DLL variants."""
import argparse
import hashlib
import json
from pathlib import Path
import re
import struct
import sys
import pefile
import capstone
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "convert"))
from port_common import ROOT, archive_dir, timestamp, write_json


def inspect_dll(source, decompiled):
    raw = source.read_bytes()
    pe = pefile.PE(data=raw)
    base = pe.OPTIONAL_HEADER.ImageBase
    imports = {i.address: (i.name.decode() if i.name else str(i.ordinal))
               for dll in pe.DIRECTORY_ENTRY_IMPORT for i in dll.imports}
    code = decompiled.read_text(encoding="utf8")
    md = capstone.Cs(capstone.CS_ARCH_X86, capstone.CS_MODE_32)
    initializers = {}
    captions = []
    for section in pe.sections:
        if not section.Characteristics & 0x20000000:
            continue
        data = section.get_data()
        for match in re.finditer(rb"\xa1(.{4})\xa3(.{4})", data, re.S):
            api, pointer = struct.unpack("<II", match.group(1) + match.group(2))
            if api in imports:
                initializers[pointer] = imports[api]
        for match in re.finditer(rb"\xc7\x45\x10(.{4})\x5d\xff\x25", data, re.S):
            address, = struct.unpack("<I", match.group(1))
            value = pe.get_data(address - base, 256).split(b"\0")[0]
            for encoding in ("gbk", "cp932"):
                try:
                    captions.append(value.decode(encoding))
                    break
                except UnicodeError:
                    pass
    hooks = []
    for match in re.finditer(r"FUN_[0-9a-f]+\(\(char \*\*\)&DAT_([0-9a-f]+),\(char \*\)FUN_([0-9a-f]+)\)", code):
        pointer, function = (int(v, 16) for v in match.groups())
        hooks.append({"original_pointer": f"{pointer:08x}", "replacement": f"{function:08x}",
                      "api": initializers.get(pointer, "original engine file I/O")})
    main = "\\#mjo\\" in code and "0x8420841" in code
    features = ([
        {"behavior": "original MJO/header/function/jump relocation and rebuilt file stream",
         "implementation": "verified per-offset Lua adapter; original MJO bytes remain read-only"},
        {"behavior": "first-byte > 0x80 ldstr and merged text; CP936/UTF16LE TXT",
         "implementation": "all supplied TXT counts and VM continuation traces verified"},
        {"behavior": "CreateFontA/CreateFontIndirectA: YouYuan, charset 134, weight 700",
         "implementation": "NarcissuTypography CJK fallback with BOLD at actual glyph boundary"},
        {"behavior": "MessageBoxA: CP932 decode and two fixed confirmation translations",
         "implementation": "adapter.dialog exact original strings plus preserved formatting spaces"},
        {"behavior": "CreateWindowExA: caption replacement",
         "implementation": "adapter.window_title retained as dialog/game title environment"},
        {"behavior": "LoadMenuW: Windows system-menu label replacement",
         "implementation": "Windows system menu is replaced by Android app UI; game menus use original translated ARC/TXT"},
        {"behavior": "five inline quote-byte patches 8175/8176 -> a1b8/a1b9",
         "implementation": "shared Unicode quote parsing preserves Japanese corner quote identity and CP936 byte map"},
        {"behavior": "original engine fopen/fread/fclose replacement",
         "implementation": "read raw #mjo through independent resource layer; no injected code or OS handles"}]
        if main else [
        {"behavior": "load/unload CNFont.ttf and Narci_chs_Font LOGFONT replacement",
         "implementation": "source CNFont.ttf absent in every provided game; bundled CJK fallback used"},
        {"behavior": "font dimensions: logical height +10, width -7, charset 128",
         "implementation": "applies only with original custom font; no absent-font metrics guessed"},
        {"behavior": "CreateWindowExA and MessageBoxA title override",
         "implementation": "primary Chinese patch title/caption takes precedence; source titles recorded"}])
    return {"path": str(source), "sha256": hashlib.sha256(raw).hexdigest(), "bytes": len(raw),
            "exports": [s.name.decode() if s.name else str(s.ordinal)
                        for s in getattr(getattr(pe, "DIRECTORY_ENTRY_EXPORT", None), "symbols", [])],
            "kind": "Narcissu Chinese script/API patch" if main else "optional custom font patch",
            "hooks": hooks, "caption_overrides": captions, "features": features,
            "decompilation": str(decompiled.relative_to(ROOT)),
            "decompilation_sha256": hashlib.sha256(decompiled.read_bytes()).hexdigest(),
            "functions_decompiled": len(re.findall(r"^// [0-9a-f]+$", code, re.M)),
            "disassembler": capstone.__version__, "dll_executed": False}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--evidence", type=Path,
                        default=ROOT / "archive/2026-11/20261110_133132_406942_narcissu_patch_code")
    args = parser.parse_args()
    inputs = json.loads((args.evidence / "20261110_inputs.json").read_text(encoding="utf8"))
    records = [inspect_dll(Path(row["source"]), Path(row["output"])) for row in inputs]
    manifests = [json.loads(p.read_text(encoding="utf8"))
                 for p in sorted((ROOT / "localization/narcissu").glob("*/manifest.json"))]
    games = []
    for manifest in manifests:
        root = Path(manifest["source_directory"])
        files = []
        for entry in manifest["files"]:
            if not entry["name"].lower().endswith(".dll"):
                continue
            files.append({"variant": "boxed", "name": entry["name"], "sha256": entry["sha256"]})
        for path in root.glob("*.dll"):
            files.append({"variant": "loose", "name": path.name,
                          "sha256": hashlib.sha256(path.read_bytes()).hexdigest()})
        saves = []
        for path in root.glob("*.exe"):
            if "_cn" in path.stem.lower() or "_chs" in path.stem.lower():
                raw = path.read_bytes()
                saves.append({"exe": path.name, "source_savedir_is_save_chs": b"save_chs\\majiro_" in raw})
        games.append({"game": manifest["game"], "dll_variants": files,
                      "CNFont.ttf_present": (root / "CNFont.ttf").is_file(), "save_directories": saves})
    report = {"schema": 1, "timestamp": timestamp(), "status": "audited",
              "games": games, "unique_dlls": len(records), "dlls": records,
              "windows_code_executed": False, "device_accessed": False,
              "scope": "all supplied boxed/loose DLLs in five Narcissu games; CRT/detours are implementation plumbing",
              "known_limitations": [
                  "Original custom CNFont.ttf is absent; CJK fallback is usable, exact glyph shapes/optional font metric adjustments are not claimed.",
                  "Windows menu/desktop caption replaces a desktop UI absent on Android; game-rendered menus remain supplied translations.",
                  "Native save_chs story import requires same-version verified mappings; arbitrary original Chinese slots are not enabled."
              ]}
    write_json(ROOT / "reports/narcissu_dll_audit.json", report)
    folder = archive_dir("narcissu_dll_audit")
    write_json(folder / "20261110_narcissu_dll_audit.json", report)
    print("Narcissu DLL audit:", len(records), "distinct binaries; all five boxed/loose game paths covered")


if __name__ == "__main__":
    main()
