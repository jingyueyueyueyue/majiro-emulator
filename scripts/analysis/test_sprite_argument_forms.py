"""Exercise every observed shape of 17 variable-arity sprite families.

This is a real-provider input-shape check, not an original-pixel comparison.
Native table/phase evidence is linked separately instead of inferred.
"""
from __future__ import annotations
from collections import defaultdict
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "convert"))
from port_common import ROOT, archive_dir, timestamp, write_json, sha256
from audit_player_semantics import registered_providers


def main():
    previous = json.loads((ROOT / "reports/player_semantics.json").read_text(encoding="utf8"))
    additional = json.loads((ROOT / "reports/new_sample_scan.json").read_text(encoding="utf8"))
    names = json.loads((ROOT / "references/majiro-py/known_hashes.json").read_text(encoding="utf8"))["syscalls"]
    observed = defaultdict(set)
    for row in previous["required_syscalls"]:
        name = row.get("name", "")
        if not name or not name.startswith("$sprite"):
            continue
        for values in row.get("signatures", {}).values():
            for signature in values:
                observed[name].add((signature["argc"], signature["opcode"]))
    for sites in additional["call_sites"].values():
        for signature in sites["signatures"]:
            name = names.get(signature["hash"], "")
            if name.startswith("$sprite"):
                observed[name].add((signature["argc"], signature["opcode"]))
    families = {name: sorted(forms) for name, forms in observed.items()
                if len(forms) > 1 and name not in ("$sprite_push", "$sprite_pop")}
    assert len(families) == 17, len(families)
    cases = []
    for name, forms in sorted(families.items()):
        for argc, opcode in forms:
            requests = []
            lua, _, _, system = registered_providers(overrides={
                "graphics": lambda request: requests.append(request) or "OK"})
            state = system["state"]
            for page, width, height in ((800, 512, 512), (900, 1280, 720)):
                state["pages"][page] = lua.table_from({"width": width, "height": height, "depth": 32})
            state["frontbuffer"] = 900
            providers = system["providers"]
            providers["$page_create_file_withalfa"] = lambda vm, args: 800
            vm = lua.table();vm["frame"] = lambda self: None
            first = providers["$sprite_create"](vm, lua.table_from([800, 0, 0, 8, 8]))
            second = providers["$sprite_create"](vm, lua.table_from([800, 0, 0, 8, 8]))
            state["sprites"][first]["x"] = 10;state["sprites"][first]["y"] = 20
            args = [first]
            if name.startswith("$sprite_create"):
                args = ["fixture.rct" if "file" in name else 800]
                if argc == 3:
                    args += [1, 2]
                elif argc >= 5:
                    args += [1, 2, 8, 8]
                    if argc == 6:
                        args += [900]
            elif name in ("$sprite_priority_high", "$sprite_priority_low"):
                if argc == 2:
                    args += [second]
            elif name == "$sprite_paste":
                args += [900]
                if argc == 4:
                    args += [10, 20]
            elif name == "$sprite_move":
                args += [30, 40]
                if argc == 4:
                    args += [10]
            elif name in ("$sprite_xmodify_set", "$sprite_ymodify_set", "$sprite_rotate", "$sprite_alfa_set"):
                args += [90 if name.endswith("rotate") else 128 if name.endswith("alfa_set") else 0.5]
                if argc >= 3:
                    args += [10]
                if argc == 4:
                    args += [1]
            else:
                for i in range(argc - 1):
                    if i % 2:
                        args += [10]
                    else:
                        args += [i // 2 if "animate" in name else (i * 13) % 256
                                 if "alfa" in name else 0.5 + i / 32]
            assert len(args) == argc, (name, argc, args)
            result = providers[name](vm, lua.table_from(args))
            if name.startswith("$sprite_create"):
                assert state["sprites"][result] is not None
            else:
                assert result == 0, (name, argc, result)
            cases.append({"name": name, "argc": argc, "opcode": opcode, "status": "accepted_by_real_provider"})
    report = {"schema": 1, "timestamp": timestamp(), "status": "passed",
              "families": families, "family_count": len(families), "cases": cases,
              "shape_count": len(cases),
              "runtime_sha256": sha256(ROOT / "player/runtime/majiro_sprites.lua"),
              "native_evidence_reports": [
                  "reports/new_graphics_abi_tests.json", "reports/new_sprite_sequence_tests.json",
                  "reports/sprite_variable_geometry_tests.json"],
              "scope": "all observed argument-count/discard forms for 17 real sprite provider families",
              "limits": "acceptance is not native semantic or pixel equivalence; only linked native scopes count as original evidence"}
    folder = archive_dir("sprite_argument_forms")
    write_json(folder / "20261110_sprite_argument_forms.json", report)
    write_json(ROOT / "reports/sprite_argument_form_tests.json", report)
    print("sprite input shapes:", len(families), "families,", len(cases), "observed forms accepted")


if __name__ == "__main__":
    main()
