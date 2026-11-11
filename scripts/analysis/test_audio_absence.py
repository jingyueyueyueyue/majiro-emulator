"""Absent source audio follows original production behavior; missing prepared data still fails."""
import struct
import unicorn
from original_x86 import Original
from lua_bridge import create_lua
from test_audio_paths import original_resolve
from majiro_arc import Catalogue
from port_common import ROOT, write_json


def missing_effect(address, handle):
    o = Original()
    request = o.memory(bytes(12) + b"suzumushi\0")
    o.machine.mem_write(handle, struct.pack("<I", 0x11223344))
    assert o.read(0x55e208, 4) == o.read(0x55e210, 4) == bytes(4)
    stops = []
    def boundary(uc, at, size, data):
        esp = uc.reg_read(o.reg.UC_X86_REG_ESP)
        target = struct.unpack("<I", o.read(esp, 4))[0]
        if at == 0x4853e0:
            stops.append(struct.unpack("<I", o.read(esp+4, 4))[0])
        uc.reg_write(o.reg.UC_X86_REG_EAX, 0)
        uc.reg_write(o.reg.UC_X86_REG_ESP, esp+4)
        uc.reg_write(o.reg.UC_X86_REG_EIP, target)
    for at in (0x47f880, 0x4853e0, 0x46c330):
        o.machine.hook_add(unicorn.UC_HOOK_CODE, boundary, begin=at, end=at)
    o.syscall(address, [request, 0])
    assert stops == [0x11223344]
    assert o.read(handle, 4) == bytes(4)
    return {"wrapper": hex(address), "previous_stopped": True, "handle_after": 0}


def run():
    available = set(Catalogue(ROOT).by_name)
    references = []
    for request in ("02itiko_0054", "suzumushi"):
        name, probes = original_resolve(request, available)
        assert name is None
        references.append({"request": request, "original_lookup": probes, "source_member": None})
    original = [missing_effect(address, handle) for address, handle in (
        (0x46d9c0, 0x5b73fc), (0x46da20, 0x5b7404),
        (0x46dcd0, 0x5b7408), (0x46df80, 0x5b740c))]
    lua = create_lua()
    providers = {}
    actions = []
    state = lua.table_from({"settings": {"soundvolume": 1000, "voicevolume": 1000,
                                        "musicvolume": 1000}}, recursive=True)
    options = lua.table_from({"resource_names": {"fixture.ogg": True}}, recursive=True)
    options["now"] = lambda: 0
    options["media_asset"] = lambda name: lua.table_from({"file": name, "duration_ms": 1000})
    options["audio"] = lambda action, spec: actions.append((action, spec["id"])) or True
    lua.execute((ROOT / "runtime/majiro_media.lua").read_text(encoding="utf-8"))["install"](
        lambda name, fn: providers.update({name: fn}), state, options)
    vm = lua.table()
    vm["fail"] = lua.eval("function(self,text)error(text)end")
    cases = []
    for channel, request in (("sound", "suzumushi"), ("sound2", "suzumushi"),
                             ("sound3", "suzumushi"), ("sound4", "suzumushi"),
                             ("voice", "02itiko_0054")):
        name = "$voice_raw" if channel == "voice" else "$" + channel
        providers[name](vm, lua.table_from(["fixture"]))
        before = state["media"]["generation"]
        actions.clear()
        assert providers[name](vm, lua.table_from([request])) == 0
        assert actions == [("stop", channel)], (channel, actions)
        assert state["media"]["channels"][channel] is None
        assert state["media"]["generation"] == before
        cases.append({"channel": channel, "request": request, "stopped_without_replacement": True})
    # A member present in the source index cannot be silently discarded if
    # its converted/encoded runtime resource is missing.
    options["media_asset"] = lambda name: None
    try:
        providers["$sound"](vm, lua.table_from(["fixture"]))
    except Exception as error:
        assert "audio resource missing" in str(error)
    else:
        raise AssertionError("present source member without prepared data was accepted")
    report = {"status": "passed", "source_exe_sha256": Original.IDENTITY,
              "source_absent_references": references, "original_effect_wrappers": original,
              "runtime_cases": cases, "present_but_unprepared_remains_error": True,
              "scope": "actual original production SE paths and original filename lookup; no substituted sounds"}
    write_json(ROOT / "reports/audio_absence_tests.json", report)
    print("source-absent audio: 4 original SE wrappers, 5 runtime channels; prepared-data errors preserved")


if __name__ == "__main__":
    run()
