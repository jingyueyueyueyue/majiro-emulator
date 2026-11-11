"""Original read-skip state through page clearing and actual next-text updates."""
from pathlib import Path
import struct
import sys

sys.path[:0] = [str(Path(__file__).resolve().parents[1] / name) for name in ("convert", "build")]
from port_common import ROOT, archive_dir, read_json, write_json, timestamp, sha256
from run_background_compatibility import source_identity
from original_text import TextOracle
from original_x86 import Original
from audit_player_semantics import registered_providers
import unicorn
from unicorn import x86_const as reg


def original_case(sample, entries, read):
    oracle = TextOracle(sample, entries)
    oracle.configure()
    def keyboard(uc, at, size, unused):
        sp = uc.reg_read(reg.UC_X86_REG_ESP)
        uc.reg_write(reg.UC_X86_REG_EAX, 0)
        uc.reg_write(reg.UC_X86_REG_EIP, oracle.read_u32(sp))
        uc.reg_write(reg.UC_X86_REG_ESP, sp + 4)
    for at in (0x44BBA0, 0x44BBF0, 0x44BC30):
        oracle.machine.hook_add(unicorn.UC_HOOK_CODE, keyboard, begin=at, end=at)
    for at, value in ((0x4DC344, 1), (0x4DC364, int(read)),
                      (0x4DC35C, 0), (0x4DC360, 1)):
        oracle.write_u32(at, value)
    clear = oracle.instruction("ctrl", "w")
    assert oracle.read_u32(0x4DC364) == int(read)
    assert oracle.read_u32(0x4DC360) == 1
    entry = next(e for e in entries if e["name"] == "$is_fast_mode")
    result = oracle.execute("syscall", [], struct.pack("<IH", int(entry["hash"], 16), 0),
                            budget=200000)
    assert result == [(0, int(read))]
    return {"read_before": read, "read_after_clear": oracle.read_u32(0x4DC364),
            "native_query_result": result, "clear_events": clear["events"],
            "original_ctrl_helper": oracle.ctrl_va}


def runtime_case(prefix, read, negative=False):
    lua, _, _, system = registered_providers()
    if prefix == "runtime":
        def module(name):
            if name.startswith("scripts/"):
                return lua.execute((ROOT / "player/tables" / Path(name).name).read_text(encoding="utf8"))
            return lua.execute((ROOT / name).read_text(encoding="utf8"))
        options = lua.table_from({"names": {}, "width": 1280, "height": 720,
                                 "config_files": {}, "config_lines": {}, "environment": {},
                                 "resource_names": {}, "image_metadata": {}, "movies": {}}, recursive=True)
        options["modules"] = module
        options["now"] = lambda: 0
        system = module("runtime/majiro_syscalls.lua")["new"](options)
    vm_module = lua.execute((ROOT / prefix / "majiro_vm.lua").read_text(encoding="utf8"))
    ops = lua.execute((ROOT / "player/tables/opcodes.lua").read_text(encoding="utf8"))
    vm = lua.eval("""function(mod,ops,system)
      local script={name='clear-skip-fixture',source_sha256='isolated-fixture',main=0,
        functions={},code={{0,0x191},{2,0x82c,0}}}
      local vm=mod.new{opcodes=ops,loader=function()return script end,syscall=system.invoke}
      vm:begin(script.name);return vm
    end""")(vm_module, ops, system)
    if negative:
        # Restore exactly the previous premature reset inside the text
        # implementation; all public providers and assertions stay intact.
        source = (ROOT / prefix / "majiro_text.lua").read_text(encoding="utf8")
        anchor = "state.reading_positions={}"
        assert source.count(anchor) == 1
        source = source.replace(anchor, anchor + ";state.current_text_read=false")
        options = lua.table_from({"environment": {}})
        options["now"] = lambda: 0
        system["text"] = lua.execute(source)["install"](
            system["state"], options, lambda *args: None, system["providers"])
    state, settings = system["state"], system["state"]["settings"]
    state["current_text_read"] = read
    settings["fast_read_mode"] = True
    settings["fast_mode_config"] = 1
    system["text"]["execute"](vm, "ctrl", "w")
    after_clear = state["current_text_read"]
    actual = system["providers"]["$is_fast_mode"](vm, lua.table())
    if negative:
        assert read and actual == 0 and not settings["fast_read_mode"]
        return {"runtime": prefix, "negative_control": True,
                "premature_clear_reproduced": True, "status": "rejected_old_behavior"}
    assert bool(after_clear) == read
    assert actual == int(read)
    assert bool(settings["fast_read_mode"]) == read
    # After a genuine next text arrives, its own saved read mark determines
    # skip. Keeping the old status during an empty transition must never
    # make new unread dialogue skippable.
    state["progress"]["read"]["clear-skip-fixture:10"] = True
    vm["current_offset"] = 10
    system["text"]["execute"](vm, "text", "previously read")
    settings["fast_read_mode"] = True
    assert state["current_text_read"] is True
    assert system["providers"]["$is_fast_mode"](vm, lua.table()) == 1
    system["text"]["execute"](vm, "ctrl", "w")
    assert system["providers"]["$is_fast_mode"](vm, lua.table()) == 1
    vm["current_offset"] = 20
    system["text"]["execute"](vm, "text", "new unread")
    assert state["current_text_read"] is False
    assert system["providers"]["$is_fast_mode"](vm, lua.table()) == 0
    assert not settings["fast_read_mode"]
    return {"runtime": prefix, "read_before": read, "preserved_through_clear": True,
            "next_read_text_skips": True, "next_unread_text_stops": True, "status": "passed"}


def main():
    before = source_identity()
    folder = archive_dir("read_skip_page_clear")
    sample = next(s for s in read_json(ROOT / "reports/opcode_dispatch.json")["samples"]
                  if s["sample"] == "RURINOKASANE")
    assert sample["exe_sha256"] == Original.IDENTITY
    entries = next(s["entries"] for s in read_json(ROOT / "reports/syscall_dispatch.json")["samples"]
                   if s["sample"] == sample["sample"])
    report = {"timestamp": timestamp(), "status": "running", "source_identity": before,
              "original_exe_sha256": sample["exe_sha256"], "original": [], "runtime": [],
              "negative_controls": [], "device_accessed": False, "player_saves_used": False,
              "scope": "actual original clear/query state plus both runtime copies and next-text read ownership",
              "evidence": str(folder.relative_to(ROOT))}
    for read in (False, True):
        report["original"].append(original_case(sample, entries, read))
        for prefix in ("player/runtime", "runtime"):
            report["runtime"].append(runtime_case(prefix, read))
    for prefix in ("player/runtime", "runtime"):
        report["negative_controls"].append(runtime_case(prefix, True, negative=True))
    report.update(status="passed", source_unchanged=source_identity() == before,
                  source_sha256={str(p.relative_to(ROOT)): sha256(p) for p in
                                 (ROOT / "player/runtime/majiro_text.lua", ROOT / "runtime/majiro_text.lua")})
    assert report["source_unchanged"]
    write_json(folder / "report.json", report)
    write_json(ROOT / "reports/read_skip_page_clear.json", report)
    print("2 original states, 4 runtime page transitions and 2 old-reset negative controls passed")


if __name__ == "__main__":
    main()
