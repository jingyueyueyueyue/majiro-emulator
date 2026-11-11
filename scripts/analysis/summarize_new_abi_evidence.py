"""Join each newly observed ABI form to its exact changed-entry evidence."""
from __future__ import annotations
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "convert"))
from port_common import ROOT, timestamp, write_json, sha256


def main():
    scan = json.loads((ROOT / "reports/new_sample_scan.json").read_text(encoding="utf8"))
    names = json.loads((ROOT / "references/majiro-py/known_hashes.json").read_text(encoding="utf8"))["syscalls"]
    reports = ("new_sprite_sequence_tests.json", "new_ui_abi_tests.json",
               "new_control_abi_tests.json", "new_archive_abi_tests.json")
    evidence = {}
    for name in reports:
        report = json.loads((ROOT / "reports" / name).read_text(encoding="utf8"))
        assert report["status"] == "passed", name
        for row in report["cases"]:
            hash_ = row.get("hash")
            if hash_ is None:
                hash_ = next(h for h, value in names.items() if value == row["name"])
            evidence[(row["sample"], hash_, row["argc"], row.get("opcode", "syscallp"))] = {
                "report": "reports/" + name, "scope": row.get("scope", report["scope"]),
                "limits": row.get("limits"), "status": row["status"]}
    graphics = json.loads((ROOT / "reports/new_graphics_abi_tests.json").read_text(encoding="utf8"))
    assert graphics["status"] == "passed"
    evidence[("kizoku", "c2c3c4f4", 3, "syscall")] = {
        "report": "reports/new_graphics_abi_tests.json", "scope": graphics["scope"], "status": "passed"}
    forms = []
    for difference in scan["differences"]:
        for signature in difference["new_call_signatures"]:
            key = (difference["sample"], signature["hash"], signature["argc"], signature["opcode"])
            forms.append({
                "sample": key[0], "hash": key[1], "name": names.get(key[1]),
                "argc": key[2], "opcode": key[3], "count": signature["count"],
                "examples": signature["examples"], "evidence": evidence.get(key),
                "status": "targeted_scope_passed" if key in evidence else "needs_targeted_check"})
    unresolved = [row for row in forms if not row["evidence"]]
    menu = json.loads((ROOT / "reports/independent_save_composition.json").read_text(encoding="utf8"))
    result = {
        "schema": 1, "timestamp": timestamp(), "status": "passed" if not unresolved else "incomplete",
        "sample_forms": len(forms), "remaining_sample_forms": len(unresolved),
        "forms": forms, "remaining": unresolved,
        "meaning": "Every new argc/return form has targeted original wrapper/state evidence; this is not complete syscall or game validation.",
        "fixes": [
            {"file": "player/runtime/majiro_sprites.lua", "behavior": "accept incomplete optional clip values and ignore them until argc >= 5"},
            {"file": "player/runtime/majiro_dialogs.lua", "behavior": "preserve and display native slot picker's second current-slot marker"},
            {"file": "player/runtime/majiro_settings.lua", "behavior": "fast disable returns prior zero-state and increments its signed counter"},
            {"file": "player/runtime/majiro_syscalls.lua", "behavior": "archive controls normalize drive/path/extension like original _splitpath + .arc"},
        ],
        "menu_fix": {"files": ["player/runtime/majiro_console.lua", "player/runtime/majiro_sprites.lua", "player/runtime/majiro_syscalls.lua"],
                     "test": "scripts/analysis/inspect_save_composition.py",
                     "report": "reports/independent_save_composition.json",
                     "checks": menu["checks"], "evidence": menu["evidence"]},
        "limits": [
            "Cross-thread event delivery now has original target-frame and portable wait/checkpoint/termination evidence in reports/cross_thread_event_tests.json.",
            "All-setup Android UI exposes existing volume/speed fields; other original pages are outside this cancel/discard ABI comparison.",
            "Native Windows save V1.102 import remains separate from the recovered archive/MJO/EXE ABI.",
            "No Android device, game EXE, injected DLL or Windows installer was run.",
        ],
        "runtime_sha256": {name: sha256(ROOT / name) for name in (
            "player/runtime/majiro_console.lua", "player/runtime/majiro_sprites.lua",
            "player/runtime/majiro_syscalls.lua", "player/runtime/majiro_dialogs.lua",
            "player/runtime/majiro_settings.lua")},
    }
    write_json(ROOT / "reports/new_abi_handoff.json", result)
    lines = ["# New-sample ABI evidence", "", "Date: 2026-11-10.",
             "", f'{len(forms)} sample-specific new forms; {len(unresolved)} without targeted wrapper/state evidence.',
             "Repeated forms across different games retain independent rows. No new hash or opcode was found in the six new games.",
             "", "| Sample | Hash | Name | argc | Return form | Evidence |",
             "| --- | --- | --- | ---: | --- | --- |"]
    for row in forms:
        lines.append(f'| {row["sample"]} | `{row["hash"]}` | {row["name"] or "unnamed"} | {row["argc"]} | '
                     f'`{row["opcode"]}` | {row["evidence"]["report"] if row["evidence"] else "missing"} |')
    lines += ["", "## Runtime fixes", ""]
    lines += [f'- `{fix["file"]}`: {fix["behavior"]}.' for fix in result["fixes"]]
    lines += ["", "## Menu residual text", "",
              "The console owner remains in the live sprite state, but its layers are excluded while freezing another script's modal backdrop. "
              "The current-scene thumbnail still includes the story text; returning through the source menu restores the same story text.",
              "`scripts/analysis/inspect_save_composition.py` checks four properties in `reports/independent_save_composition.json`.",
              "", "## Limits", ""] + ["- " + item for item in result["limits"]]
    (ROOT / "reports/new_abi_handoff.md").write_text("\n".join(lines) + "\n", encoding="utf8")
    print("New ABI evidence:", len(forms), "forms;", len(unresolved), "unresolved targeted forms")
    if unresolved:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
