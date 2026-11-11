"""Recover named and unnamed reachable hash selectors, stopping before handlers."""
from __future__ import annotations
from collections import deque
import hashlib
import json
from pathlib import Path
import sys
import time

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "convert"))
from port_common import ROOT, read_json, timestamp, write_json
from compare_majiro_samples import reference_exe


def recover(game, names):
    import capstone
    import pefile
    import unicorn
    from unicorn import x86_const as reg

    exe = reference_exe(game)
    raw = Path(exe["path"]).read_bytes()
    pe = pefile.PE(data=raw)
    base = pe.OPTIONAL_HEADER.ImageBase
    candidates = exe["known_hash_candidates"]
    root = min(row["va"] for row in candidates)
    root_row = next(row for row in candidates if row["va"] == root)
    hash_register = root_row["register"]
    register_id = getattr(reg, "UC_X86_REG_" + hash_register.upper())
    md = capstone.Cs(capstone.CS_ARCH_X86, capstone.CS_MODE_32)
    md.detail = True
    machine = unicorn.Uc(unicorn.UC_ARCH_X86, unicorn.UC_MODE_32)
    machine.mem_map(base, (pe.OPTIONAL_HEADER.SizeOfImage + 4095) & ~4095)
    machine.mem_write(base, pe.get_memory_mapped_image())
    machine.mem_map(0x10000000, 0x20000)
    for name in ("EAX", "EBX", "ECX", "EDX", "ESI", "EDI", "EBP"):
        machine.reg_write(getattr(reg, "UC_X86_REG_" + name), 0x10001000)
    machine.reg_write(reg.UC_X86_REG_ESP, 0x10010000)
    machine.reg_write(reg.UC_X86_REG_EFLAGS, 2)
    initial = machine.context_save()
    cache = {}
    comparisons = {}
    selected = {}
    last_compare = None
    query = 0

    def stop(kind, target=None):
        selected.update(kind=kind, target=target)
        machine.emu_stop()

    def hook(uc, at, size, _):
        nonlocal last_compare
        ins = cache.get(at)
        if ins is None:
            ins = next(md.disasm(bytes(uc.mem_read(at, 16)), at))
            cache[at] = ins
        if (ins.mnemonic == "cmp" and len(ins.operands) == 2
                and ins.operands[0].type == capstone.CS_OP_REG
                and md.reg_name(ins.operands[0].reg) == hash_register
                and ins.operands[1].type == capstone.CS_OP_IMM):
            value = ins.operands[1].imm & 0xFFFFFFFF
            if uc.reg_read(register_id) != query:
                stop("hash_register_changed")
                return
            comparisons.setdefault(value, set()).add(at)
            last_compare = value
        zero = bool(uc.reg_read(reg.UC_X86_REG_EFLAGS) & 64)
        if ins.mnemonic == "je" and zero and last_compare == query:
            stop("equality", ins.operands[0].imm)
        elif ins.mnemonic == "jne" and zero and last_compare == query:
            stop("fallthrough", at + size)
        elif ins.mnemonic == "call":
            stop("unmatched_call_boundary")
        elif ins.mnemonic == "ret":
            stop("unmatched_return")
        elif ins.mnemonic in ("int", "int3"):
            stop("unexpected_trap")

    machine.hook_add(unicorn.UC_HOOK_CODE, hook)
    known_hashes = {int(row["hash"], 16) for row in candidates}
    script_hashes = {int(h, 16) for h in game["syscalls"]}
    pending = deque(sorted(known_hashes | script_hashes | {0, 0x7FFFFFFF, 0x80000000, 0xFFFFFFFF}))
    queued_thresholds = set()
    results = {}
    failures = []
    began = time.monotonic()
    while pending:
        query = pending.popleft()
        if query in results:
            continue
        machine.context_restore(initial)
        machine.reg_write(register_id, query)
        selected.clear()
        last_compare = None
        try:
            machine.emu_start(root, 0, count=200)
        except Exception as error:
            selected.update(kind="error", error=str(error))
        if not selected:
            selected.update(kind="instruction_budget")
        results[query] = dict(selected)
        if selected["kind"] in ("error", "instruction_budget", "hash_register_changed", "unexpected_trap"):
            failures.append({"query": f"{query:08x}", **selected})
        new_values = set(comparisons) - queued_thresholds
        queued_thresholds.update(new_values)
        # Probe exact thresholds and both neighboring intervals. This explores
        # branches that no name in the pinned dictionary or game script uses.
        for value in sorted(new_values):
            pending.extend((value, (value - 1) & 0xFFFFFFFF, (value + 1) & 0xFFFFFFFF))
        if len(results) > 10000:
            raise RuntimeError("hash selector exploration exceeded bounded input budget")
    entries = []
    for value, result in sorted(results.items()):
        if result["kind"] not in ("equality", "fallthrough"):
            continue
        entries.append({
            "hash": f"{value:08x}", "name": names.get(f"{value:08x}"),
            "compare_vas": sorted(comparisons.get(value, set())),
            "selected_va": result["target"], "selection_kind": result["kind"],
            "used_by_sample_scripts": value in script_hashes,
            "handler_semantics_verified": False,
        })
    recovered = {int(entry["hash"], 16) for entry in entries}
    missing_known = sorted(known_hashes - recovered)
    unresolved_scripts = sorted(script_hashes - recovered)
    return {
        "sample": Path(game["directory"]).name, "exe_sha256": hashlib.sha256(raw).hexdigest(),
        "selector_root_va": root, "hash_register": hash_register,
        "status": "passed" if not failures and not missing_known and not unresolved_scripts else "incomplete",
        "known_dictionary_entries": len(known_hashes), "recovered_entries": len(entries),
        "unnamed_entries": sum(entry["name"] is None for entry in entries),
        "tested_hash_values": len(results), "seconds": round(time.monotonic() - began, 3),
        "missing_known_entries": [f"{h:08x}" for h in missing_known],
        "script_calls_without_recovered_entry": [f"{h:08x}" for h in unresolved_scripts],
        "failures": failures, "entries": entries, "handler_execution": False,
        "limit": "reachable direct compare selector only; dynamic registrations and ABI semantics not established",
    }


def main():
    inventory = read_json(ROOT / "reports/majiro_samples.json")
    names = read_json(ROOT / "references/majiro-py/known_hashes.json")["syscalls"]
    rows = []
    for game in inventory["games"]:
        row = recover(game, names)
        rows.append(row)
        print(f"{row['sample']}: {row['status']}, entries {row['recovered_entries']}, "
              f"unnamed {row['unnamed_entries']}, unresolved scripts "
              f"{len(row['script_calls_without_recovered_entry'])}", flush=True)
    passed = all(row["status"] == "passed" for row in rows)
    write_json(ROOT / "reports/syscall_dispatch.json", {
        "schema": 1, "timestamp": timestamp(), "status": "passed" if passed else "incomplete",
        "samples": rows, "runtime_compatible": False,
        "scope": "named plus unnamed direct-compare selector entries; no handler execution",
    })
    return 0 if passed else 1


if __name__ == "__main__":
    raise SystemExit(main())
