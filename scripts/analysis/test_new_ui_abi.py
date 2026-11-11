"""Targeted original console, capture and native-dialog ABI forms."""
from __future__ import annotations
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "convert"))
from port_common import ROOT, archive_dir, timestamp, write_json, sha256
from test_new_graphics_abi import FunctionFixture
from audit_player_semantics import registered_providers


def main():
    scan = json.loads((ROOT / "reports/new_sample_scan.json").read_text(encoding="utf8"))
    kizoku = next(s for s in scan["opcode_samples"] if s["sample"] == "kizoku")
    happy = json.loads((ROOT / "reports/happy_wrapper_recovery.json").read_text(encoding="utf8"))
    rows = []
    f = FunctionFixture(kizoku)
    f.uc.mem_write(0x10020000, bytes(12) + b"fixture-font\0")
    f.hook(0x416020, lambda sp: f.top)
    f.hook(0x416040, lambda sp: setattr(f, "result", f.read(sp + 4)) or 0)
    formatting = []
    f.hook(0x4371e0, lambda sp: formatting.extend(f.read(sp + i * 4) for i in range(3, 8)) or 0)
    f.hook(0x40ff80, lambda sp: 0)
    f.call(0x4105b0, [0x10020000, 32, 18, 44])
    assert formatting[:4] == [32, 18, 44, 0], formatting
    assert formatting[4] == 0x1002000c and f.result == 0
    lua, _, _, system = registered_providers(overrides={"text_controls": {"legacy_font_defaults": True}})
    assert system["providers"]["$console_font"](lua.table(), lua.table_from(["fixture-font", 32, 18, 44])) == 0
    spec = system["state"]["console"]["font_spec"]
    assert [spec[i] for i in range(2, 6)] == formatting[:4]
    rows.append({"sample": "kizoku", "hash": "06b3c8ac", "argc": 4, "opcode": "syscallp",
                 "status": "passed", "scope": "size/width/line-step and omitted style=0"})
    f = FunctionFixture(kizoku)
    f.hook(0x416020, lambda sp: f.top)
    f.hook(0x416040, lambda sp: setattr(f, "result", f.read(sp + 4)) or 0)
    callbacks = [11, 22, 33, 44, 55, 66, 77]
    f.call(0x420750, callbacks)
    slots = [0x4f593c, 0x4f5958, 0x4f5948, 0x4f5960, 0x4f5930, 0x4f5964, 0x4f5928]
    assert [f.read(at) for at in slots] == callbacks and f.result == 0
    system["providers"]["$set_mouse_capture_left"](lua.table(), lua.table_from(callbacks))
    assert list(system["state"]["mouse_capture"]["left"].values()) == callbacks
    rows.append({"sample": "kizoku", "hash": "163c0878", "argc": 7, "opcode": "syscallp",
                 "status": "passed", "scope": "all seven callback IDs and registration return"})
    # Original production dialog skips formatting/UI and returns 0; debug
    # mode forwards exactly argc to both formatter and dialog helper.
    for testplay in (False, True):
        f = FunctionFixture(kizoku)
        calls = []
        f.hook(0x406fc0, lambda sp: 0 if testplay else 1)
        f.hook(0x401200, lambda sp: calls.append(("format", f.read(sp + 4))) or 0)
        f.hook(0x401260, lambda sp: calls.append(("dialog", f.read(sp + 4), f.read(sp + 8))) or 1)
        f.hook(0x416040, lambda sp: setattr(f, "result", f.read(sp + 4)) or 0)
        f.call(0x4013a0, [1, 2, 3, 4])
        assert f.result == 0
        assert calls == ([("format", 4), ("dialog", 4, 16)] if testplay else [])
    rows.append({"sample": "kizoku", "hash": "df644f85", "argc": 4, "opcode": "syscallp",
                 "status": "passed", "scope": "production suppression and debug formatter argc forwarding"})
    recon = next(s for s in scan["opcode_samples"] if s["sample"] == "reconquista")
    f = FunctionFixture(recon)
    f.hook(0x40a610, lambda sp: 1)
    f.hook(0x41c760, lambda sp: setattr(f, "result", f.read(sp + 4)) or 0)
    f.call(0x4016f0, [1, 2, 3, 4])
    assert f.result == 0
    rows.append({"sample": "reconquista", "hash": "df644f85", "argc": 4, "opcode": "syscallp",
                 "status": "passed", "scope": "production suppression ignores formatting args and returns zero",
                 "limits": "debug log file/UI path is separately unverified"})
    f = FunctionFixture({"path": happy["initialized_reference"],
                         "exe_sha256": happy["initialized_reference_sha256"]})
    f.hook(0x41d940, lambda sp: f.top)
    f.hook(0x41d960, lambda sp: setattr(f, "result", f.read(sp + 4)) or 0)
    f.hook(0x43f8d0, lambda sp: 0)
    f.hook(0x43f6c0, lambda sp: 1)
    f.put(0x47a320, 0x10060000)
    f.hook(0x10060000, lambda sp: 1, cleanup=8)
    f.put(0x47a374, 0x10060010)
    f.hook(0x10060010, lambda sp: f.put(0x518c38, 123) or 1, cleanup=20)
    f.call(0x4233b0, [0, 122])
    assert f.read(0x518c1c) == 0 and f.read(0x518c0c) == 122 and f.result == 123
    loading = False
    dialog = []
    lua, _, _, system = registered_providers(overrides={
        "dialog": lambda *args: dialog.append(args) or "pending"})
    vm = lua.table_from({"thread_id": 1, "context_generation": 0})
    result = system["providers"][0x9af10167](vm, lua.table_from([0, 122]), True)
    assert result == (None, True)
    assert dialog and dialog[0][2] == 327681 and dialog[0][4] == "Save game"
    marker=json.loads(dialog[0][3])
    assert marker["schema"]==1 and marker["current_slot"]==122 and marker["loading"]is False
    rows.append({"sample": "happy_princess", "hash": "9af10167", "argc": 2, "opcode": "syscall",
                 "status": "passed", "scope": "save/load flag, current-slot marker and selected-slot return",
                 "native_result": f.result})
    # The all-setup command has no arguments and always pushes zero after
    # the configuration dialog returns. Cancellation must not persist changes.
    f = FunctionFixture({"path": happy["initialized_reference"],
                         "exe_sha256": happy["initialized_reference_sha256"]})
    native_dialogs = []
    f.hook(0x4353e0, lambda sp: native_dialogs.append("all_setup") or 0)
    f.hook(0x41d960, lambda sp: setattr(f, "result", f.read(sp + 4)) or 0)
    f.call(0x4369a0, [])
    assert f.result == 0 and native_dialogs == ["all_setup"]
    actions = []
    lua, _, _, system = registered_providers(overrides={
        "dialog": lambda action, *args: actions.append(action) or ("pending" if action == "begin" else "data ")})
    vm_module = lua.execute((ROOT / "player/runtime/majiro_vm.lua").read_text("utf8"))
    opcodes = lua.execute((ROOT / "player/tables/opcodes.lua").read_text("utf8"))
    vm = lua.eval("""function(mod,ops,invoke)
      local script={name='setup-fixture.mjo',main=0,functions={},code={{0,0x191}}}
      local v=mod.new{opcodes=ops,loader=function()return script end,syscall=invoke}
      v:begin(script.name);return v
    end""")(vm_module, opcodes, system["invoke"])
    settings = system["state"]["settings"]
    before = {key: settings[key] for key in ("musicvolume", "soundvolume", "voicevolume", "mojispeed", "autospeed", "effectspeed")}
    assert system["providers"]["$all_setup_dialog"](vm, lua.table(), False) == (None, True)
    assert system["dialogs"]["poll"](vm) is True
    assert len(vm["stack"]) == 0 and vm["waiting"] is None
    assert actions == ["begin", "poll"]
    assert before == {key: settings[key] for key in before}
    rows.append({"sample": "happy_princess", "hash": "4ea2d1fc", "argc": 0, "opcode": "syscallp",
                 "status": "passed", "scope": "argument-free dialog dispatch, cancel keeps settings, zero discarded",
                 "limits": "Android all-setup page only exposes volume/speed; other original pages remain separate UI coverage"})
    report = {"schema": 1, "timestamp": timestamp(), "status": "passed", "cases": rows,
              "scope": "new argument/return forms only; no Windows UI executed",
              "source_sha256": {name: sha256(ROOT / name) for name in (
                  "player/runtime/majiro_console.lua", "player/runtime/majiro_capture.lua",
                  "player/runtime/majiro_dialogs.lua")}}
    folder = archive_dir("new_ui_abi")
    write_json(folder / "20261110_new_ui_abi.json", report)
    write_json(ROOT / "reports/new_ui_abi_tests.json", report)
    print("new UI ABI:", len(rows), "forms passed, current-slot marker implemented")


if __name__ == "__main__":
    main()
