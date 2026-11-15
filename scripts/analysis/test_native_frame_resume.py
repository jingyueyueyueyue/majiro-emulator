"""V1.103/V1.105 original writers -> native frame projection -> typed continuation."""
from pathlib import Path
import hashlib
import json
import struct
import sys

sys.path[:0] = [str(Path(__file__).resolve().parents[1] / part)
                for part in ("analysis", "convert")]
import test_emulator_host_restore as base
from test_host_load_atomicity import captured_options
from test_formats import mjo
from original_story_save import StorySaveOracle, LegacyStoryWriter
from original_story_102 import EarlyStoryWriter
from original_story_media import PROFILES
from majiro_mjo import Script
from majiro_story_save import parse_story_save
from native_pixels import Pixels
from port_common import ROOT, archive_dir, read_json, sha256, timestamp, write_json


def source(name, child):
    if child:
        code = struct.pack("<HHBBHHB", 0x836, 2, 0, 0, 0x829, 1, 0)
        code += struct.pack("<HHIhH", 0x802, 96, 42, 0, 0x118)
        code += struct.pack("<HHIhH", 0x802, 96, 1, -2, 0x118)
        code += struct.pack("<HHIhHH", 0x802, 96, 2, -3, 0x118, 0x82b)
    else:
        code = struct.pack("<HHHIIH", 0x829, 0, 0x80f, 42, 0, 2)
        code += struct.pack("<HHIhH", 0x1b0, 32, 123, -1, 0x82b)
    return Script(name, mjo(code, entries=((42 if child else 41, 0),)))


def original(sample, legacy, folder):
    oracle = (EarlyStoryWriter if sample["sample"]=="kizoku"else LegacyStoryWriter if legacy else StorySaveOracle)(sample)
    oracle.fixture(1, False)
    names = ["LEGACY0.MJO", "LEGACY1.MJO"] if legacy else ["FIXTURE0.MJO", "FIXTURE1.MJO"]
    scripts = [source(names[0], False), source(names[1], True)]
    slots = b"".join(struct.pack("<4I", 0, 0, 3, value) for value in (20, 10, 2, 7, 9))
    fields = [(16, 0, 0, 0, 0, 0, 0, 0, 0, 0), (11, 3, 5, 0, 0, 0, 0, 0, 0, 0)]
    if legacy:
        oracle.write_u32(oracle.HEADER + 0xa4, 5)
        oracle.machine.mem_write(oracle.DATA + 0x33000, slots)
        for i, values in enumerate(fields):
            oracle.machine.mem_write(oracle.FRAME_BLOCKS + i * 168 + 128, struct.pack("<10I", *values))
    else:
        oracle.machine.mem_write(oracle.DATA + 0x33000, slots)
        for i, (script, values) in enumerate(zip(scripts, fields)):
            code = oracle.DATA + 0x50000 + i * 0x1000
            oracle.machine.mem_write(code, script.code)
            oracle.write_u32(oracle.DATA + 0x40000 + i * 0x200 + 0x90, len(script.code))
            values = [code + values[0], *values[1:]]
            oracle.machine.mem_write(oracle.context + 0x28 + i * 0x50, struct.pack("<10I", *values))
        profile = PROFILES[sample["sample"]]
        for address in profile["music"][2:]:
            oracle.machine.mem_write(address, bytes(256))
        for row in profile["channels"]:
            oracle.machine.mem_write(row[2], bytes(128)); oracle.machine.mem_write(row[3], bytes(128))
            oracle.write_u32(row[4], 0)
        for _, address in profile["integers"]:
            oracle.write_u32(address, 0)
        oracle.write_u32(0x4e6848, 0)
        oracle.machine.mem_write(0x54d820, bytes(40000))
    raw = oracle.capture_and_write()
    path = folder / (sample["sample"] + "_nested.sav")
    path.write_bytes(raw)
    parsed = parse_story_save(raw)
    assert [r["fields"] for r in parsed["frames"]] == [list(row) for row in fields]
    if not legacy:
        destination = oracle.load(raw)
        assert destination["frames"][1]["04"] == 3 and destination["frames"][1]["08"] == 5
    parsed["source_sha256"] = hashlib.sha256(raw).hexdigest()
    return parsed, scripts


def run_case(sample, legacy, folder, fault):
    native, scripts = original(sample, legacy, folder)
    lua, module, system, vm, store, transport, original_script = base.fixture()
    codec = module("runtime/majiro_codec.lua")
    options = captured_options(lua, system["providers"]["$do_load"])
    options["decode_bytes"]=lua.eval("""function(convert)return function(raw)
      return convert((raw:gsub('.',function(c)return string.format('%02x',c:byte())end)))
    end end""")(lambda text:bytes.fromhex(text).decode("cp932"))
    native_pixels = Pixels()
    options["graphics"] = native_pixels
    original_script["code"][3] = lua.table_from([100, 0x800, 0])
    original_script["code"][4] = lua.table_from([106, 0x82b])
    vm["scripts"][original_script["name"]]["positions"][100] = 3
    vm["scripts"][original_script["name"]]["positions"][106] = 4
    system["state"]["events"]["HOT_RESET"] = lua.table_from([{
        "script": original_script["name"], "offset": 100, "hash": 101, "owner": 1}], recursive=True)
    tables = {s.name.lower(): lua.table_from(s.runtime_data(), recursive=True) for s in scripts}
    vm["loader"] = lambda name: codec["clone"](tables.get(name.lower(), original_script))
    manifest_scripts = {s.name.lower(): {"sha256": s.sha256, "version_identity": "a" * 64} for s in scripts}
    manifest_scripts[original_script["name"].lower()] = {"sha256": original_script["source_sha256"],
                                                        "version_identity": "a" * 64}
    manifest = lua.table_from({"schema": 1, "source_identity": "a" * 64, "native_exe_sha256": sample["exe_sha256"],
        "save_sha256": native["source_sha256"], "verified_same_script_version": True,
        "restore_kind": "native_story", "scripts": manifest_scripts}, recursive=True)
    json_module = module("runtime/majiro_json.lua")
    plan = system["saves"]["plan_native_import"](vm, json_module["decode"](json.dumps(native)), manifest)
    before = codec["encode"](vm["export_state"](vm))
    fail = {"armed": fault}
    def graphics(request):
        if request == "checkpoint_commit" and fail["armed"]:
            fail["armed"] = False
            return "ERR injected native scene commit failure"
        return native_pixels(request)
    options["graphics"] = graphics
    if fault:
        try:
            system["saves"]["import_native"](vm, plan)
        except Exception as error:
            assert "graphics commit failed" in str(error), error
        else:
            raise AssertionError("native reconstruction commit failure accepted")
        assert codec["encode"](vm["export_state"](vm)) == before
    system["saves"]["import_native"](vm, plan)
    assert len(vm["frames"]) == 2 and vm["frame"](vm)["args"][1] == 10
    for _ in range(20):
        if vm["status"] == "finished":
            break
        vm["step"](vm)
    assert vm["status"] == "finished" and vm["globals"][1][123] == 46
    return {"format": native["format"], "original_exe_sha256": sample["exe_sha256"],
            "frames": 2, "stack_values": 5, "result": 46, "rollback_retry": fault, "status": "passed"}


def main():
    folder = archive_dir("native_frame_resume")
    samples = read_json(ROOT / "reports/opcode_dispatch.json")["samples"]
    rows = []
    for name, legacy in (("kizoku",True),("narci_himeko", True), ("narci_onetwo", False)):
        sample = next(s for s in samples if s["sample"] == name)
        for fault in (False, True):
            rows.append(run_case(sample, legacy, folder, fault))
    report = {"status": "passed", "timestamp": timestamp(), "cases": rows, "device_accessed": False,
              "original_writers_executed": True, "v105_original_reader_executed": True,
              "evidence": str(folder.relative_to(ROOT))}
    write_json(folder / "report.json", report)
    write_json(ROOT / "reports/native_frame_resume_tests.json", report)
    print("Native frames: 6 V1.102/V1.103/V1.105 writer/restore/continue/rollback cases passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
