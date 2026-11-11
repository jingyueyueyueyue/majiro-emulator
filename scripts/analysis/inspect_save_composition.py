"""Read-only independent-player trace of scene snapshots entering save UI.

Uses prepared resources and public source inputs. The legacy setup helper is
only an asset/I/O fixture: all VM/syscall/runtime and pixels code is replaced
in memory with player/ modules. No Artemis files or device state are written.
"""
from __future__ import annotations

import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "convert"))
from port_common import ROOT, archive_dir, lua_value, write_json, timestamp
from lua_bridge import create_lua, setup_runtime, table_value
from native_pixels import Pixels
import native_pixels
from model_session import Session
from test_settings import click_id


class IndependentLua:
    def __init__(self, lua):
        self.lua = lua
        self.replacements = {}
        for path in (ROOT / "runtime").glob("*.lua"):
            independent = ROOT / "player/runtime" / path.name
            if independent.exists():
                self.replacements[path.read_text(encoding="utf8")] = independent.read_text(encoding="utf8")
        self.replacements[(ROOT / "runtime/majiro_syscalls.lua").read_text(encoding="utf8")] = (
            (ROOT / "player/runtime/majiro_syscalls.lua").read_text(encoding="utf8").replace(
                "function M.new(options)", """function M.new(options)
  options.modules=function(name)
    return assert(loadstring(INDEPENDENT_MODULE(name),name))()
  end
  for key,value in pairs(INDEPENDENT_PROFILE)do
    if key~='numeric'then options[key]=value end
  end""", 1))
        self.replacements[(ROOT / "runtime/majiro_vm.lua").read_text(encoding="utf8")] = (
            (ROOT / "player/runtime/majiro_vm.lua").read_text(encoding="utf8").replace(
                "function M.new(options)", "function M.new(options)\n options.numeric_profile='sse32'\n", 1))

    def execute(self, text):
        return self.lua.execute(self.replacements.get(text, text))

    def __getattr__(self, name):
        return getattr(self.lua, name)


def run():
    folder = archive_dir("independent_save_composition")
    lua = create_lua()
    profiles = json.loads((ROOT / "config/player_engine_profiles.json").read_text(encoding="utf8"))
    profile = profiles["executables"]["2bec58f9fd7f924b5ca403d894ec609a265a57efdfca25397f95a5752b3fcd46"]
    lua.globals()["INDEPENDENT_PROFILE"] = lua.table_from(profile, recursive=True)

    def module(name):
        if name.startswith("runtime/"):
            path = ROOT / "player" / name
        elif name == "scripts/tile_ai.lua":
            path = ROOT / "player/tables/tile_ai.lua"
        else:
            path = ROOT / name
        return path.read_text(encoding="utf8")

    lua.globals()["INDEPENDENT_MODULE"] = module
    wrapper = IndependentLua(lua)
    original_pixels = native_pixels.Pixels
    native_pixels.Pixels = lambda port=False: original_pixels(port=False)
    try:
        _, vm, system, clock = setup_runtime(wrapper, False, save_root=folder / "saves")
    finally:
        native_pixels.Pixels = original_pixels
    session = Session.__new__(Session)
    session.lua, session.vm, session.system, session.clock = lua, vm, system, clock
    state = system["state"]
    stages = []
    native = Pixels.__new__(Pixels)
    import ctypes
    from port_common import player_pixels_library
    native.dll = ctypes.CDLL(str(player_pixels_library()))
    native.dll.RuriPixels.argtypes = [ctypes.c_char_p]
    native.dll.RuriPixels.restype = ctypes.c_void_p
    native.dll.Delete.argtypes = [ctypes.c_void_p]
    native.dll.Delete.restype = None

    def capture(label):
        system["sprites"]["update"]()
        sprites = []
        rows = []
        backdrop_rows = []
        for identifier in state["sprite_order"].values():
            sprite = state["sprites"][identifier]
            if not sprite:
                continue
            data = table_value(sprite)
            data["identifier"] = identifier
            sprites.append(data)
            if (sprite["destination"] == state["frontbuffer"]
                    and sprite["anti"] < 255 and (sprite["hidden"] or 0) == 0):
                row = [sprite[key] for key in ("page", "sx", "sy", "width", "height",
                                              "x", "y", "xscale", "yscale", "rotate", "anti")]
                rows.append(" ".join(map(str, row)) + (" 1" if sprite["extended"] else " 0"))
                if sprite["source_script"] != state["console"]["owner_script"]:
                    backdrop_rows.append(rows[-1])
        image = folder / ("20261110_" + label + ".png")
        assert native(f'render {state["frontbuffer"]} {image}\n' + "\n".join(rows)) == "OK"
        backdrop_image = folder / ("20261110_" + label + "_background.png")
        assert native(f'render {state["frontbuffer"]} {backdrop_image}\n' + "\n".join(backdrop_rows)) == "OK"
        base = folder / ("20261110_" + label + "_base.png")
        assert native(f'png {state["frontbuffer"]} {base}') == "OK"
        snapshot = state["screen_snapshot"]
        if snapshot:
            assert native(f'png {snapshot} {folder / ("20261110_" + label + "_snapshot.png")}') == "OK"
        stages.append({"label": label, "sprites": sprites, "snapshot": snapshot,
                       "frontbuffer": state["frontbuffer"], "console": table_value(state["console"]),
                       "journal": list(state["graphics_journal"].values()), "screenshot": str(image)})

    vm["begin"](vm, "start.mjo")
    assert session.advance() == "ui", session.summary()
    assert session.click(1) == "text", session.summary()
    for _ in range(4):
        assert session.page() == "text", session.summary()
    clock["ms"] += 500
    session.event()
    capture("story")
    state["reading_wait"] = False
    session.event(x=1000, y=650, buttons=2)
    assert session.advance(stop_at_text=False) == "ui", session.summary()
    capture("pause")
    assert click_id(session, 0) == "ui", session.summary()
    for _ in range(40):
        clock["ms"] += 20
        session.event()
        assert vm["run"](vm, 10000) != "blocked", session.summary()
    capture("save")
    from PIL import Image
    area = state["console"]["configuration"]
    rectangle = (area[2], area[3], area[2] + area[4], area[3] + area[5])
    with Image.open(folder / "20261110_story.png") as story:
        assert story.convert("RGB").crop(rectangle).getbbox(), "story fixture has no visible text"
    with Image.open(folder / "20261110_save_snapshot.png") as backdrop, \
            Image.open(folder / "20261110_story_background.png") as background:
        assert backdrop.convert("RGB").tobytes() == background.convert("RGB").tobytes(), \
            "modal backdrop contains a layer beyond the story scene"
    assert state["console"]["owner_script"] == "console.mjo"
    assert all((s["hidden"] or 0) < 0 for s in state["sprites"].values()
               if s["source_script"] == state["console"]["owner_script"])
    original_text = state["console"]["text"]
    result = click_id(session, 1003)
    if result == "ui":
        session.clock["ms"] += 500
        session.event(x=1100, y=650, buttons=2)
        result = session.advance()
    assert result == "text", session.summary()
    assert state["console"]["text"] == original_text, "modal return changed story text"
    assert state["screen_snapshot"] is None, "modal snapshot survived return"
    capture("returned_story")
    report = {"schema": 1, "timestamp": timestamp(), "stages": stages,
              "scope": "independent player VM/pixels and source menu flow; Windows asset fixture only",
              "android_device_accessed": False, "runtime_sources_changed": False, "evidence": str(folder),
              "checks": ["story text visible", "modal backdrop equals the story scene without console layers",
                         "original console sprites retained but hidden during modal",
                         "public modal return restores the same story text and releases snapshot"]}
    write_json(folder / "20261110_trace.json", report)
    write_json(ROOT / "reports/independent_save_composition.json", report)
    print(folder)


if __name__ == "__main__":
    run()
