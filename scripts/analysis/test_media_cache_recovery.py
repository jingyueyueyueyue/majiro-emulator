"""Exercise saved media recovery, real cache files, and native cache eviction."""
from pathlib import Path
import json
import subprocess
import sys

sys.path[:0] = [str(Path(__file__).resolve().parents[1] / part)
                for part in ("analysis", "build", "convert")]
import test_emulator_host_restore as base
from test_host_load_atomicity import captured_options
from build_player_core import msvc_environment
from port_common import ROOT, archive_dir, read_json, sha256, timestamp, write_json


class Boundary:
    def __init__(self, lua):
        self.lua = lua
        self.epoch = 0
        self.files = set()
        self.assets = []
        self.events = []
        self.fail = None

    def asset(self, name):
        self.assets.append(name)
        if name == self.fail:
            raise OSError("injected archive read failure: " + name)
        path = f"/host-session-{self.epoch}/cache/media/{name}"
        self.files.add(path)
        return self.lua.table_from({"file": path, "duration_ms": 250 if name == "loop.ogg" else 1000})

    def audio(self, action, spec):
        self.events.append({key: spec[key] for key in ("file", "loop_file", "offset_ms", "volume")})
        self.events[-1]["action"] = action
        if action == "play":
            for path in (spec["file"], spec["loop_file"]):
                if path and path not in self.files:
                    raise FileNotFoundError(2, "No such file or directory", path)
        return action != "completed"

    def evict(self):
        self.epoch += 1
        self.files.clear()
        self.assets.clear()
        self.events.clear()


def fixture():
    lua, module, system, vm, store, transport, script = base.fixture()
    options = captured_options(lua, system["providers"]["$do_load"])
    boundary = Boundary(lua)
    options["resource_names"] = lua.table_from({name: True for name in ("fixture.ogg", "loop.ogg", "live.ogg")})
    options["audio_aliases"] = lua.table_from({"alias": "fixture"})
    options["audio"], options["media_asset"] = boundary.audio, boundary.asset
    settings = system["state"]["settings"]
    for field in ("musicvolume", "voicevolume", "soundvolume"):
        settings[field] = 1000
    settings["talk_male"], settings["talk_female"] = 1, 1
    system["state"]["pending_voice_skip"] = False
    call = lambda name, *args: system["providers"][name](vm, lua.table_from(args))
    return lua, system, vm, transport, options, boundary, call


def saved_case(channel, external, legacy, extended=False, elapsed=50, paused=False):
    lua, system, vm, transport, options, boundary, call = fixture()
    state = system["state"]
    if extended:
        call("$ext" + channel, "alias", "loop")
    else:
        call("$" + channel, "alias", 1)
    spec = state["media"]["channels"][channel]
    if paused:
        spec["paused_at"] = 100 + elapsed
        spec["pause_requested"] = True
    if legacy:
        spec["resource"], spec["loop_resource"] = None, None
    options["set_clock"](100 + elapsed)
    call("$save_point")
    call("$do_extsave") if external else call("$do_save", 101)
    state["media"]["channels"][channel] = None
    state["settings"]["musicvolume"] = 350
    boundary.evict()
    call("$do_extload") if external else call("$do_load", 101)
    spec = state["media"]["channels"][channel]
    assert spec["resource"] == "fixture.ogg" and "/host-session-1/" in spec["file"]
    events = [event for event in boundary.events if event["action"] == "play"]
    assert len(events) == 1 and not transport["staged"]
    expected = "loop.ogg" if extended and elapsed >= 1000 else "fixture.ogg"
    assert events[0]["file"].endswith("/" + expected), events
    expected_offset = (elapsed - 1000) % 250 if extended and elapsed >= 1000 else elapsed
    assert events[0]["offset_ms"] == expected_offset, events
    if channel == "music":
        assert events[0]["volume"] == 350, events
    if paused:
        assert boundary.events[-1]["action"] == "pause"
    if extended:
        assert spec["loop_resource"] == "loop.ogg" and "/host-session-1/" in spec["loop_file"]
    return {"channel": channel, "external": external, "legacy": legacy, "extended": extended,
            "elapsed": elapsed, "paused": paused, "offset": expected_offset, "status": "passed"}


def stack_and_replacement():
    rows = []
    for legacy in (False, True):
        lua, system, vm, transport, options, boundary, call = fixture()
        call("$extmusic", "alias", "loop")
        spec = system["state"]["media"]["channels"]["music"]
        if legacy:
            spec["resource"], spec["loop_resource"] = None, None
        call("$music_push")
        call("$save_point")
        call("$do_save", 101)
        boundary.evict()
        call("$do_load", 101)
        call("$music_pop")
        assert boundary.assets == ["fixture.ogg", "loop.ogg"]
        assert boundary.events[-1]["file"].startswith("/host-session-1/")
        rows.append({"case": "saved_music_stack_pop", "legacy": legacy, "status": "passed"})
    for extended in (False, True):
        lua, system, vm, transport, options, boundary, call = fixture()
        call("$extmusic", "alias", "loop")
        call("$extmusic_replace", "loop", "fixture") if extended else call("$music_replace", "loop", 1)
        call("$save_point")
        call("$do_save", 101)
        system["state"]["media"]["channels"]["music"] = None
        boundary.evict()
        call("$do_load", 101)
        spec = system["state"]["media"]["channels"]["music"]
        assert spec["resource"] == "loop.ogg" and spec["file"].endswith("/loop.ogg")
        assert spec["loop_resource"] == ("fixture.ogg" if extended else None)
        rows.append({"case": "replaced_music_restore", "extended": extended, "status": "passed"})
    # Missing assets must still reject loading and preserve a usable live state.
    for failed_member in ("fixture.ogg", "loop.ogg"):
        lua, system, vm, transport, options, boundary, call = fixture()
        call("$extmusic", "alias", "loop")
        call("$save_point")
        call("$do_save", 101)
        boundary.evict()
        call("$music", "live", 1)
        vm["globals"][1][123] = 777
        boundary.fail = failed_member
        ok, why = lua.eval("function(f,v)local ok,e=pcall(f,v,{101});return ok,tostring(e)end")(
            system["providers"]["$do_load"], vm)
        assert not ok and failed_member in why
        assert not transport["staged"] and vm["globals"][1][123] == 777
        assert system["state"]["media"]["channels"]["music"]["name"] == "live"
        boundary.fail = None
        call("$do_load", 101)
        rows.append({"case": "missing_archive_member_rollback_and_retry", "member": failed_member, "status": "passed"})
    return rows


def file_checks(folder):
    java = Path(read_json(ROOT / "reports/android_toolchain.json")["java"]) / "bin"
    java_source = ROOT / "player/android/src/org/majiro/player/MediaCache.java"
    environment = msvc_environment()
    compiler = Path(environment["VCTOOLSINSTALLDIR"]) / "bin/Hostx64/x64/cl.exe"
    executable = folder / "media_cache_probe.exe"
    commands = [
        ("java_compile", [str(java / "javac.exe"), "-encoding", "UTF-8", "-d", str(folder),
                          str(java_source), str(ROOT / "scripts/analysis/MediaCacheChecks.java")], None),
        ("java_run", [str(java / "java.exe"), "-cp", str(folder),
                      "org.majiro.player.MediaCacheChecks", str(folder / "java_files")], None),
        ("native_compile", [str(compiler), "/nologo", "/std:c++20", "/EHsc", "/utf-8",
                            str(ROOT / "scripts/analysis/media_cache_probe.cpp"),
                            "/Fe:" + str(executable), "/Fo:" + str(folder / "probe.obj")], environment),
        ("native_run", [str(executable), str(folder / "native_files")], None),
    ]
    results = {}
    for name, command, env in commands:
        result = subprocess.run(command, cwd=folder, env=env, capture_output=True, timeout=60)
        (folder / (name + ".txt")).write_bytes(result.stdout + result.stderr)
        if result.returncode:
            raise RuntimeError(name + " failed: " + str(folder / (name + ".txt")))
        if name.endswith("_run"):
            results[name] = json.loads(result.stdout)
    return results


def main():
    folder = archive_dir("media_cache_recovery")
    sources = ("player/runtime/majiro_media.lua", "player/android/game_host.cpp", "player/android/media_cache.hpp",
               "player/android/src/org/majiro/player/MediaCache.java",
               "player/android/src/org/majiro/player/GameBridge.java")
    before = {name: sha256(ROOT / name) for name in sources}
    channels = ["music", "voice", "sound", "sound2", "sound3", "sound4"]
    channels += [prefix + str(i) for prefix in ("voice_ch_", "sound_ch_") for i in range(20)]
    rows = [saved_case(channel, external, legacy) for channel in channels
            for external in (False, True) for legacy in (False, True)]
    rows += [saved_case(channel, external, legacy, True, elapsed, paused)
             for channel in ("music", "sound", "sound2", "sound3", "sound4")
             for external in (False, True) for legacy in (False, True)
             for elapsed in (50, 1375) for paused in (False, True)]
    rows += stack_and_replacement()
    physical = file_checks(folder)
    assert before == {name: sha256(ROOT / name) for name in sources}
    report = {"status": "passed", "timestamp": timestamp(), "cases": rows,
              "runtime_cases": len(rows), "physical_cache_checks": physical, "source_sha256": before,
              "device_accessed": False, "user_saves_accessed": False, "evidence": str(folder.relative_to(ROOT)),
              "scope": "production Lua local/external restore, legacy saves, all 46 channels, intro/loop offsets, pause, stack, replacement, rollback/retry; production Java atomic cache and C++ file validation on real files"}
    write_json(folder / "result.json", report)
    write_json(ROOT / "reports/media_cache_recovery_tests.json", report)
    print(json.dumps({key: report[key] for key in ("status", "runtime_cases", "physical_cache_checks", "evidence")}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
