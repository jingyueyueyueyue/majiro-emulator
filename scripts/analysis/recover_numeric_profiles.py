"""Classify engine numeric behavior by executing discriminating original cases."""
from __future__ import annotations

import json
from pathlib import Path
import struct
import sys

from original_opcode import OpcodeOracle
from original_strings import StringOracle
from original_modes import ModeOracle
from original_text import TextOracle
from original_voice_playback import VoicePlaybackOracle
from original_screen import ScreenOracle
from original_graphics_remaining import GraphicsOracle

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "convert"))
from port_common import ROOT, atomic_bytes, lua_value, timestamp, write_json


def recover(sample, entries=None):
    oracle = OpcodeOracle(sample)
    probes = {
        "integer_ge_true": ("cge.i", ((0, 2), (0, 1))),
        "real_lt_true": ("clt.r", ((0, 1), (0, 2))),
        "real_integer_rounding": ("ceq.r", ((0, 16777217), (1, 0x4B800000))),
        "unordered_equality": ("ceq.r", ((0, 1), (1, 0x7FC00000))),
        "infinite_to_integer": ("conv.i", ((1, 0x7F800000),)),
        "integer_zero_divisor": ("div.i", ((0, 1), (0, 0))),
        "real_zero_divisor": ("div.r", ((0, 1), (0, 0))),
    }
    observed = {key: oracle.execute(name, operands) for key, (name, operands) in probes.items()}
    minimum = oracle.execute("syscall", [(0, -4), (0, 3), (0, 7)],
                             struct.pack("<IH", 0x3D053317, 3), budget=20000)
    if minimum == [(0, 3)]:
        minmax_arguments = 2
    elif minimum == [(0, 0xFFFFFFFC)]:
        minmax_arguments = 0  # All supplied arguments.
    else:
        raise ValueError(f"{sample['sample']}: unknown min/max argument behavior: {minimum}")
    observed["minimum_three_values"] = minimum
    common = {"integer_ge_true": [(0, 0xFFFFFFFF)],
              "real_lt_true": [(0, 0xFFFFFFFF)],
              "integer_zero_divisor": [(0, 10000)],
              "real_zero_divisor": [(1, 0x47C35000)]}
    for key, expected in common.items():
        if observed[key] != expected:
            raise ValueError(f"{sample['sample']}: unknown numeric behavior at {key}: {observed[key]}")
    variants = {
        "sse32": {"real_integer_rounding": [(0, 1)], "unordered_equality": [(0, 0)],
                  "infinite_to_integer": [(0, 0x80000000)]},
        "x87": {"real_integer_rounding": [(0, 0)], "unordered_equality": [(0, 1)],
                "infinite_to_integer": [(0, 0)]},
    }
    matches = [profile for profile, expected in variants.items()
               if all(observed[key] == value for key, value in expected.items())]
    if len(matches) != 1:
        raise ValueError(f"{sample['sample']}: numeric profile is not classified: {observed}")
    string_oracle = StringOracle(sample)
    greek = [string_oracle.string(text, index) for index, text in enumerate(("\u0391", "\u03b1"))]
    folded = string_oracle.execute("ceq.s", [(2, address) for address in greek], budget=100000)
    if folded not in ([(0, 0)], [(0, 1)]):
        raise ValueError(f"{sample['sample']}: string folding is unclassified")
    string_case = "cp932" if folded == [(0, 1)] else "latin_fullwidth"
    observed["greek_case_equality"] = folded
    if entries is None:
        dispatch = json.loads((ROOT/"reports/syscall_dispatch.json").read_text(encoding="utf-8"))
        entries = next(row["entries"] for row in dispatch["samples"]
                       if row["exe_sha256"] == sample["exe_sha256"])
    def string_call(name, args):
        entry = next(entry for entry in entries if entry["name"] == name)
        values = [(2, string_oracle.string(value, i)) for i, value in enumerate(args)]
        return string_oracle.execute("syscall", list(reversed(values)),
                                     struct.pack("<IH", int(entry["hash"], 16), len(values)), budget=200000)
    integer_overflow = string_call("$atoi", ("+2147483648",))
    if integer_overflow not in ([(0, 0x80000000)], [(0, 0x7FFFFFFF)]):
        raise ValueError("unclassified decimal conversion overflow")
    empty_search = string_call("$strstr", ("", ""))
    if empty_search not in ([(0, 0xFFFFFFFF)], [(0, 0)]):
        raise ValueError("unclassified empty-string search")
    observed["decimal_overflow"] = integer_overflow
    observed["empty_string_search"] = empty_search
    left,right=string_oracle.string("A",0),string_oracle.string("A",1)
    case_pointer=string_oracle.DATA+0x50000
    string_oracle.machine.mem_write(case_pointer,struct.pack("<4I",0x11f91fd3,2,2,left))
    string_oracle.execute("bge.case",[(2,right)],struct.pack("<i",42),
        context_fields={8:1,0x20:case_pointer},budget=200000)
    case_strings=string_oracle.read_u32(string_oracle.frame)-(string_oracle.operand-2)==48
    observed["string_case_ge_equal_destination"]=string_oracle.read_u32(string_oracle.frame)-(string_oracle.operand-2)
    mode_oracle=ModeOracle(sample,entries)
    ultra_disable_returns_previous=mode_oracle.disable_returns_previous()
    text_controls=TextOracle(sample,entries).profile()
    if any(row["name"]=="$get_voice_setup"for row in entries):
        voice_controls=VoicePlaybackOracle(sample,entries).profile()
    else:
        from original_legacy_voice import LegacyVoiceProfileOracle
        voice_controls=LegacyVoiceProfileOracle(sample,entries).profile()
    screen_controls=ScreenOracle(sample,entries).profile()
    if any(row["name"]=="$grp_mulcopy"for row in entries):
        from native_function import NativeFunctions
        import capstone
        # The profile depends only on the verified dispatch argument guard.
        # Full page/raster recovery belongs to the graphics comparison suite.
        functions=NativeFunctions(string_oracle)
        entry=next(row for row in entries if row["name"]=="$grp_mulcopy")
        root=functions.syscall_helper(entry)
        graphics_controls={"optional_mulcopy_coordinates":any(row.mnemonic=="cmp"and
            len(row.operands)==2 and row.operands[1].type==capstone.CS_OP_IMM
            and row.operands[1].imm==7 for row in functions.body(root))}
    else:graphics_controls={"mulcopy_available":False}
    dialog_cache=ROOT/"reports/dialog_controls.json"
    dialog_rows=json.loads(dialog_cache.read_text(encoding="utf8"))["samples"]if dialog_cache.exists()else[]
    dialog_controls=next((row["dialog_controls"]for row in dialog_rows if row["exe_sha256"]==sample["exe_sha256"]),None)
    if dialog_controls is None:
        from original_dialogs import DialogOracle
        dialog=DialogOracle(sample,entries)
        dialog_controls={}
        for name,key in (("$dialog_ok","reset_playback_modes"),("$dialog_error_ok","reset_error_playback_modes")):
            for address in mode_oracle.mode_slots.values():dialog.write_u32(address,1)
            dialog.call_dialog(name,["calibrate"])
            values={dialog.read_u32(address)for address in mode_oracle.mode_slots.values()}
            if values not in ({0},{1}):raise ValueError("unclassified dialog playback mode behavior")
            dialog_controls[key]=values=={0}
    return {"sample": sample["sample"], "exe_sha256": sample["exe_sha256"],
            "numeric_profile": matches[0], "minmax_arguments": minmax_arguments,
            "string_case": string_case, "string_crt_initialization": string_oracle.crt_initialization,
            "integer_parse": "wrap32" if integer_overflow == [(0, 0x80000000)] else "saturate32",
            "empty_search_absent": empty_search == [(0, 0xFFFFFFFF)],
            "case_string_relations": case_strings,
            "ultra_disable_returns_previous":ultra_disable_returns_previous,
            "ultra_fast_mode_available":"$set_ultra_fast_mode"in mode_oracle.entries,
            "text_controls":text_controls,
            "voice_controls":voice_controls,
            "screen_controls":screen_controls,
            "graphics_controls":graphics_controls,
            "dialog_controls":dialog_controls,
            "probes": observed}


def main():
    recovered = json.loads((ROOT / "reports/opcode_dispatch.json").read_text(encoding="utf-8"))
    rows = [recover(sample) for sample in recovered["samples"]]
    report = {"schema": 1, "timestamp": timestamp(), "status": "passed", "samples": rows,
              "scope": "numeric profile discriminators; full instruction equivalence tested separately"}
    write_json(ROOT / "reports/numeric_profiles.json", report)
    mapping = {row["exe_sha256"]: {"numeric": row["numeric_profile"],
                                  "minmax_arguments": row["minmax_arguments"],
                                  "string_case": row["string_case"],
                                  "integer_parse": row["integer_parse"],
                                  "empty_search_absent": row["empty_search_absent"],
                                  "case_string_relations": row["case_string_relations"],
                                  "ultra_disable_returns_previous":row["ultra_disable_returns_previous"],
                                  "text_controls":row["text_controls"],
                                  "voice_controls":row["voice_controls"],
                                  "screen_controls":row["screen_controls"],
                                  "graphics_controls":row["graphics_controls"],
                                  "dialog_controls":row["dialog_controls"]} for row in rows}
    extra=ROOT/"reports/new_engine_profiles.json"
    if extra.exists():
        supplemental=json.loads(extra.read_text(encoding="utf8"))
        mapping.update(supplemental.get("executables",{}))
    write_json(ROOT / "config/player_engine_profiles.json", {
        "schema": 1, "source": "reports/numeric_profiles.json", "executables": mapping,
        "unknown_engine": "requires_profile_recovery",
    })
    module = "-- Recovered original engine behavior; keyed by executable SHA-256.\n"
    module += "return " + lua_value(mapping) + "\n"
    atomic_bytes(ROOT / "player/runtime/majiro_engine_profiles.lua", module.encode("utf-8"))
    for row in rows:
        print(f"{row['sample']}: {row['numeric_profile']}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
