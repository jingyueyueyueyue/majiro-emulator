"""Exact pixel checkpoints and movie restore/rollback through production providers."""
from pathlib import Path
import json
import sys

sys.path[:0] = [str(Path(__file__).resolve().parents[1] / part)
                for part in ("analysis", "convert")]
from PIL import Image
import test_emulator_host_restore as base
from test_host_load_atomicity import captured_options
from lua_bridge import create_lua
from native_pixels import Pixels
from port_common import ROOT, archive_dir, sha256, timestamp, write_json


def commands(encoded):
    assert encoded.startswith("OK\n"), encoded
    result, at = [], 3
    while at < len(encoded):
        end = encoded.index("\n", at)
        length = int(encoded[at:end])
        at = end + 1
        result.append(encoded[at:at + length])
        at += length
    assert at == len(encoded)
    return result


def pixel_roundtrip(folder):
    native = Pixels()
    for command in ("create 1 4 3 0 32", "alpha 1 2 0", "fill 1 0 0 4 3 772823",
                    "fill 2 0 0 4 3 83", "drawmode 1 3", "create 3 4 3 0 8",
                    "fill 3 0 0 4 3 129", "antidata 1 3", "release 3"):
        assert native(command) == "OK", command
    before = native("checkpoint_export")
    encoded = commands(before)
    assert native("reset") == "OK"
    for command in encoded:
        assert native(command) == "OK", command
    assert native("checkpoint_export") == before
    assert native("release 1") == "OK"
    assert native("pixel 3 0 0").startswith("ERR"), "pending alpha release ownership lost"
    for data in ("R4097:00000000", "R0:00000000", "L1:0000", "R1:zzzzzzzz",
                 "R999999999999999:00000000", "L1:ffffffffR4096:00000000"):
        assert native("reset") == "OK"
        assert native("create 1 2 2 0") == "OK"
        assert native("page_pixels 1 0 " + data).startswith("ERR")
    # Incompressible literals and chunk boundaries must preserve every byte.
    path = folder / "literal.png"
    image = Image.new("RGBA", (129, 65))
    image.putdata([((i * 29) % 256, (i * 101) % 256, (i // 7) % 256, 255)
                   for i in range(129 * 65)])
    image.save(path)
    assert native("reset") == "OK"
    assert native("frame 55 " + str(path)) == "OK"
    encoded = native("checkpoint_export")
    assert native("frame 55 " + str(path)) == "OK", "second decoded frame reused an invalid handle"
    assert native("reset") == "OK"
    for command in commands(encoded):
        assert native(command) == "OK"
    assert native("checkpoint_export") == encoded
    return {"case": "native_pixels_palettes_owned_masks_literals_and_bounds", "status": "passed"}


def movie_case(folder, texture, external, failure):
    lua, module, system, vm, store, transport, script = base.fixture()
    p, state = system["providers"], system["state"]
    options = captured_options(lua, p["$do_load"])
    native = Pixels()
    live = {"active": False, "position": 375, "page": None, "reject": False}
    trace = []
    images = {}
    for value in (37, 80, 40):
        path = folder / f"frame_{value}.png"
        Image.new("RGBA", (2, 2), (value, 27, 91, 255)).save(path)
        images[value] = path

    def frame(value):
        assert native("frame 55 " + str(images[value])) == "OK"

    def movie(action, spec):
        trace.append((action, spec["resume_position_ms"]))
        if action == "checkpoint_detach":
            live["active"] = False
            return 0
        if action in ("play", "play_repeat", "play_fromto", "texture_play"):
            live["active"] = True
            if spec["resume_position_ms"] is not None:
                live["position"] = spec["resume_position_ms"]
            if texture:
                frame(int(live["position"] // 10))
            if live["reject"]:
                live["reject"] = False
                raise IOError("injected movie restart failure after effect")
            return 1
        if action == "status":
            return int(live["active"])
        if action == "texture":
            return 55 if live["active"] else 0
        if action in ("texture_width", "texture_height"):
            return 2
        return 0

    options["movie"], options["movie_position"] = movie, lambda: live["position"]
    options["graphics"] = native
    options["movies"] = lua.table_from({"clip.avi": {"file": "clip.avi", "duration_ms": 1000}}, recursive=True)
    call = lambda name, *args: p[name](vm, lua.table_from(args))
    call("$movie_texture_play" if texture else "$movie_play", "clip.avi")
    if texture:
        destination = call("$page_create", 2, 2)
        call("$grp_copy", 55, 0, 0, 2, 2, destination, 0, 0)
        frozen = native(f"pixel {destination} 0 0")
    call("$save_point")
    call("$do_extsave") if external else call("$do_save", 101)
    live["position"] = 800
    if texture:
        frame(80)
    before = native("checkpoint_export")
    codec = module("runtime/majiro_codec.lua")
    before_vm = codec["encode"](vm["export_state"](vm))
    live["reject"] = failure
    if failure:
        try:
            call("$do_extload") if external else call("$do_load", 101)
        except Exception as error:
            assert "injected movie restart failure" in str(error), error
        else:
            raise AssertionError("movie restart failure was accepted")
        assert live["active"] and live["position"] == 800, trace
        assert native("checkpoint_export") == before, "movie failure changed the live pixels"
        assert codec["encode"](vm["export_state"](vm)) == before_vm
    call("$do_extload") if external else call("$do_load", 101)
    assert live["active"] and live["position"] == 375, trace
    spec = state["media"]["movie_texture" if texture else "movie"]
    assert spec is not None and spec["position_ms"] == 375
    if texture:
        assert native(f"pixel {destination} 0 0") == frozen, "copied movie pixels were lost"
        frame(40)
        assert native(f"pixel {destination} 0 0") == frozen
        assert native("pixel 55 0 0") == "OK 40 27 91 255"
    return {"case": "movie_checkpoint", "texture": texture, "external": external,
            "failure_and_retry": failure, "status": "passed"}


def main():
    folder = archive_dir("movie_checkpoint")
    sources = ("player/runtime/majiro_save.lua", "player/runtime/majiro_media.lua",
               "player/core/page_checkpoint.hpp", "player/native/majiro_pixels.cpp")
    before = {name: sha256(ROOT / name) for name in sources}
    rows = [pixel_roundtrip(folder)]
    rows += [movie_case(folder, texture, external, failure)
             for texture in (False, True) for external in (False, True)
             for failure in (False, True)]
    assert before == {name: sha256(ROOT / name) for name in sources}
    report = {"status": "passed", "timestamp": timestamp(), "cases": rows,
              "source_sha256": before, "device_accessed": False,
              "scope": "production Lua transactions and native pixels; deterministic decoder boundary",
              "evidence": str(folder.relative_to(ROOT))}
    write_json(folder / "report.json", report)
    write_json(ROOT / "reports/movie_checkpoint_tests.json", report)
    print(json.dumps({"status": "passed", "cases": len(rows), "evidence": str(folder)}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
