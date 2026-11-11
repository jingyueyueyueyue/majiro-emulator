"""Execute legacy EXE audio controls without assuming a newer pause/stream ABI."""
from pathlib import Path
import itertools
import json
import struct
import sys

import unicorn
from unicorn import x86_const as reg
from original_calls import CallOracle
from native_function import NativeFunctions
from original_legacy_voice import LegacyVoiceProfileOracle
from audit_player_semantics import registered_providers
sys.path.insert(0, str(Path(__file__).resolve().parents[1]/"convert"))
from port_common import ROOT, read_json, timestamp, write_json


class LegacyAudioOracle(CallOracle):
    def __init__(self, sample, entries):
        super().__init__(sample)
        # Addresses identify this read-only oracle input, never a runtime game gate.
        assert sample["sample"] == "kizoku"
        self.entries = {row["name"]: row for row in entries if row["name"]}
        self.functions = NativeFunctions(self)
        self.stream = self.DATA+0x50000
        self.handle = 0x12345678
        self.direct_stop = self.DATA+0xfe000
        self.fast = False
        self.disabled = False
        self.clock = 2000
        for address, value in ((0x407ad0, lambda: int(self.fast)),
                               (0x434fa0, lambda: int(self.disabled))):
            self.machine.hook_add(unicorn.UC_HOOK_CODE,
                lambda uc, at, size, unused, get=value: self._return(0, get()),
                begin=address, end=address)

    def _import(self, uc, at, size, unused):
        name = self.import_names.get(at)
        if name in ("EnterCriticalSection", "LeaveCriticalSection"):
            return self._return(1)
        if name in ("timeGetTime", "GetTickCount"):
            return self._return(0, self.clock)
        return super()._import(uc, at, size, unused)

    def prepare(self, present, active, fast, disabled=False):
        self.fast, self.disabled = fast, disabled
        self.machine.mem_write(self.stream, bytes(0x500))
        self.write_u32(self.stream+0x418, self.handle)
        self.write_u32(self.stream+0x448, int(active))
        self.write_u32(self.stream+0x450, 777)
        self.write_u32(0x4f99c4, self.stream if present else 0)
        self.machine.mem_write(0x4f71d4, bytes(80))
        self.write_u32(0x4f71d4+7*4, self.handle if present else 0)
        self.write_u32(0x46bf2c, 413)
        # Production voice master getter used by numbered volume/fade.
        getter = self.functions.body(0x4247c0)
        self.write_u32(getter[0].operands[1].mem.disp, 733)

    def call(self, name, arguments, discard):
        uc = self.machine
        helper = self.functions.syscall_helper(self.entries[name])
        values = [(0, value & 0xffffffff) for value in reversed(arguments)]
        if name == "$sound_ch_wait":
            uc.context_restore(self.initial)
            uc.mem_write(self.context, bytes(0x28))
            uc.mem_write(self.frame, bytes(0x50))
            uc.mem_write(self.values, bytes(256))
            self.write_u32(self.frame_global, self.frame)
            self.write_u32(self.context_global, self.context)
            self.write_u32(self.context+0x24, self.values)
            self.write_u32(self.context+0x10, 1024)
            self.write_u32(self.frame+8, len(values))
            for i, (tag, bits) in enumerate(values):
                uc.mem_write(self.values+i*16, struct.pack("<4I", 0, tag, 0, bits))
            stack = self.STACK+0xf000
            uc.mem_write(stack, struct.pack("<II", self.direct_stop, len(values)))
            uc.reg_write(reg.UC_X86_REG_ESP, stack)
            uc.reg_write(reg.UC_X86_REG_ECX, len(values))
            uc.emu_start(helper, self.direct_stop, count=100000)
            assert uc.reg_read(reg.UC_X86_REG_EIP) == self.direct_stop
            pending = uc.reg_read(reg.UC_X86_REG_EAX) == 1
        else:
            self.execute("syscallp" if discard else "syscall", values,
                         struct.pack("<IH", int(self.entries[name]["hash"], 16), len(arguments)),
                         budget=100000)
            pending = False
        return {"pending": pending, "active": bool(self.read_u32(self.stream+0x448)),
                "handle": self.read_u32(0x4f71d4+7*4),
                "gain": self.read_u32(self.stream+0x450),
                "fade_ms": self.read_u32(self.stream+0x458),
                "fade_gain": (self.read_u32(self.stream+0x45c)+
                              self.read_u32(self.stream+0x460)) & 0xffffffff,
                "next_gain": self.read_u32(0x46bf2c)}


def voice_checks(oracle):
    profile = oracle.profile()
    rows = []
    for name, force, fast in itertools.product(("voice", "##nw##", "##other"), (0, 1), (False, True)):
        expected = oracle.probe(name, force, fast)
        ref, events = {}, []

        def asset(filename):
            return ref["lua"].table_from({"file": filename, "duration_ms": 100})

        lua, _, _, system = registered_providers(overrides={
            "voice_controls": profile, "environment": {"VOICEVOL": "1000"},
            "resource_names": {name.lower()+".wav": True}, "media_asset": asset,
            "audio": lambda action, spec: events.append(action) or True,
        })
        ref["lua"] = lua
        system["providers"]["$is_fast_mode"] = lambda vm, args: int(fast)
        vm = lua.table()
        media = system["state"]["media"]
        media["channels"]["voice"] = lua.table_from({
            "id": "voice", "file": "prior.wav", "generation": 1,
        })
        media["voice_tag"](vm, name, force, 1, 0, 0)
        assert bool(events.count("play")) == expected["played"], (name, force, fast, expected, events)
        assert events.count("stop") == expected["stop_count"]
        rows.append({"name": name, "force": force, "fast": fast, "original": expected})
    return rows


def run():
    sample = next(row for row in read_json(ROOT/"reports/opcode_dispatch.json")["samples"]
                  if row["sample"] == "kizoku")
    entries = next(row["entries"] for row in read_json(ROOT/"reports/syscall_dispatch.json")["samples"]
                   if row["sample"] == sample["sample"])
    oracle = LegacyAudioOracle(sample, entries)
    checks = []
    for present, active, fast, discard in itertools.product((False, True), repeat=4):
        for name, args in (
            ("$sound_ch_wait", [7]), ("$sound_ch_stop", [7]),
            ("$sound_ch_vol", [7, 413]), ("$sound_ch_fade", [7, 413, 150]),
            ("$sound_ch_fade", [7, 413, -1]),
        ):
            oracle.prepare(present, active, fast)
            expected = oracle.call(name, args, discard)
            events = []

            def audio(action, spec):
                events.append({"action": action, "gain": spec["volume"], "fade": spec["fade_ms"]})
                return active if action == "playing" else False if action == "completed" else True

            lua, _, _, system = registered_providers(overrides={"audio": audio, "now": lambda: 2000})
            system["providers"]["$is_fast_mode"] = lambda vm, args: int(fast)
            media = system["state"]["media"]
            system["state"]["settings"]["voicevolume"] = 733
            media["sound_ch_volume"] = 413
            if present:
                media["channels"]["sound_ch_7"] = lua.table_from({
                    "id": "sound_ch_7", "file": "prior.wav", "generation": 1,
                    "volume": 777, "started": 1900, "duration_ms": 100,
                })
            vm = lua.table()
            system["providers"][name](vm, lua.table_from(args))
            assert bool(vm["waiting"]) == expected["pending"], (name, present, active, fast, expected)
            assert int(media["sound_ch_volume"]) & 0xffffffff == expected["next_gain"]
            if present and name in ("$sound_ch_vol", "$sound_ch_fade"):
                actual = next(row for row in events if row["action"] == "volume")
                wanted = expected["gain"] if name == "$sound_ch_vol" else expected["fade_gain"]
                assert actual["gain"] & 0xffffffff == wanted
                if name == "$sound_ch_fade":
                    assert actual["fade"] == expected["fade_ms"]
            checks.append({"name": name, "present": present, "active": active, "fast": fast,
                           "discard": discard, "original": expected})
    voice = voice_checks(LegacyVoiceProfileOracle(sample, entries))
    report = {"status": "passed", "timestamp": timestamp(), "sample": sample["sample"],
              "exe_sha256": sample["exe_sha256"], "audio_cases": checks, "voice_cases": voice,
              "runtime_root": "player/runtime", "device_accessed": False,
              "scope": "original legacy numbered wait/stop/volume/fade and voice marker/force routing",
              "boundaries": ["fast mode", "device enabled", "clock", "single-thread locks",
                             "legacy voice VFS/decoder creation"]}
    folder = ROOT.parent/"archive/2026-11/20261111_emulator_audit_host"
    folder.mkdir(parents=True, exist_ok=True)
    write_json(folder/"20261111_legacy_audio.json", report)
    print(f"Legacy original audio: {len(checks)} controls and {len(voice)} voice cases passed")


if __name__ == "__main__":
    run()
