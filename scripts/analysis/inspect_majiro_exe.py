"""Read-only PE inventory. Static candidates are not compatibility claims."""
from __future__ import annotations
from collections import Counter
import hashlib
from pathlib import Path
import re
import struct


def embedded_engines(path: Path):
    """Return complete PE resource payloads with original Majiro signatures."""
    import pefile
    raw = path.read_bytes()
    rows = []
    for match in re.finditer(b"MZ", raw):
        offset = match.start()
        if not offset:
            continue
        try:
            pe = pefile.PE(data=raw[offset:], fast_load=True)
            size = max(s.PointerToRawData + s.SizeOfRawData for s in pe.sections)
            if not 0 < size <= 128 * 1024 * 1024 or offset + size > len(raw):
                continue
            data = raw[offset:offset + size]
            if b"MajiroObjX1.000" not in data or b"MajiroSavV" not in data:
                continue
            rows.append({"offset": offset, "size": size, "data": data,
                         "sha256": hashlib.sha256(data).hexdigest(),
                         "container_sha256": hashlib.sha256(raw).hexdigest()})
        except (pefile.PEFormatError, ValueError):
            continue
    return rows


def engine_code_sections(pe):
    """Include old .text sections missing IMAGE_SCN_MEM_EXECUTE.

    Some legacy PEs mark *every* section as read/write data, including their
    original interpreter and selector. In that case every initialized section
    supplies static candidates; bounded dispatch recovery must establish code.
    """
    executable = [section for section in pe.sections if section.Characteristics & 0x20000000]
    return executable or [section for section in pe.sections if section.SizeOfRawData > 0]


def inspect_executable(path: Path, names: dict[int, str]):
    import capstone
    import pefile

    data = path.read_bytes()
    pe = pefile.PE(data=data)
    result = {
        "path": str(path.resolve()), "sha256": hashlib.sha256(data).hexdigest(),
        "bytes": len(data), "machine": f"{pe.FILE_HEADER.Machine:04x}",
        "image_base": pe.OPTIONAL_HEADER.ImageBase,
        "signatures": sorted({x.decode("ascii") for x in re.findall(
            rb"Majiro(?:ArcV|Obj[ VX]?|SavV|SysV)[A-Za-z0-9_.]{0,24}", data)}),
        "sections": [s.Name.rstrip(b"\0").decode("ascii", "replace") for s in pe.sections],
        "imports": [i.dll.decode("ascii", "replace")
                    for i in getattr(pe, "DIRECTORY_ENTRY_IMPORT", [])],
        "status": "static_inspection_only", "runtime_verified": False,
    }
    result["boxed_patch_candidate"] = any(n.startswith(".enigma") for n in result["sections"])
    if pe.FILE_HEADER.Machine != 0x14C:
        result["dispatch_status"] = "unsupported_machine"
        return result
    md = capstone.Cs(capstone.CS_ARCH_X86, capstone.CS_MODE_32)
    compares = []
    for section in engine_code_sections(pe):
        code = section.get_data()
        for pattern in (rb"\x81[\xf8-\xff](.{4})", rb"\x3d(.{4})"):
            for match in re.finditer(pattern, code, re.DOTALL):
                value, = struct.unpack("<I", match.group(1))
                address = pe.OPTIONAL_HEADER.ImageBase + section.VirtualAddress + match.start()
                instruction = next(md.disasm(code[match.start():match.start() + 16], address), None)
                if instruction is None or instruction.mnemonic != "cmp":
                    continue
                compares.append({
                    "va": address, "hash": f"{value:08x}",
                    "register": instruction.op_str.split(",")[0],
                    "name": names.get(value),
                })
    known = sorted((r for r in compares if r["name"]), key=lambda r: r["va"])
    result["known_hash_candidates"] = known
    result["distinct_known_hashes"] = len({r["hash"] for r in known})
    result["registers"] = dict(Counter(r["register"] for r in known))
    # Include unknown immediates in the same dense comparison regions. These
    # require CFG validation: a comparison value alone is not a registration.
    clusters = []
    for row in known:
        if not clusters or row["va"] - clusters[-1][-1]["va"] > 2048:
            clusters.append([])
        clusters[-1].append(row)
    regions = []
    unknown = {}
    for rows in clusters:
        if len(rows) < 16:
            continue
        registers = {r["register"] for r in rows}
        low, high = rows[0]["va"], rows[-1]["va"]
        regions.append({"first_known_compare": low, "last_known_compare": high,
                        "known_comparisons": len(rows)})
        for r in compares:
            if low <= r["va"] <= high and r["register"] in registers and not r["name"]:
                unknown[(r["va"], r["hash"])] = r
    result["comparison_regions"] = regions
    result["unknown_compare_candidates"] = list(unknown.values())
    result["dispatch_boundary_recovery_complete"] = False
    result["warning"] = "Comparisons are candidates; handler semantics and CFG boundaries remain unverified."
    return result

