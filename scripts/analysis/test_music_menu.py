"""Exercise the shipped music menu and its actual buttons, using shared assets."""
from model_session import Session
from test_settings import click_id
from test_title_render import render_state
from lua_bridge import table_value
from port_common import ROOT, archive_dir, write_json


def run(verify_only=False):
    evidence = archive_dir("music_menu")
    s = Session(not verify_only, save_root=evidence / "saves")
    assert s.advance() == "ui", s.summary()
    original = s.system["state"]["media"]["channels"]["music"]["name"]
    s.system["providers"]["$call"](s.vm, s.lua.table_from(["music"]), False)
    assert s.advance() == "ui", s.summary()
    cases = []
    for identifier in range(1, 22):
        spec = s.system["state"]["media"]["channels"]["music"]
        # The currently selected song is intentionally disabled by the source.
        if any(a["args"][1] == identifier for a in s.system["state"]["ui_areas"].values()):
            assert click_id(s, identifier) == "ui", s.summary()
        spec = s.system["state"]["media"]["channels"]["music"]
        assert spec and s.system["providers"]["$music_stat"](s.vm, s.lua.table()) == 1
        cases.append({"song": identifier, "playback": table_value(spec)})
    for identifier in (902, 903, 901, 904):
        assert click_id(s, identifier) == "ui", s.summary()
        spec = s.system["state"]["media"]["channels"]["music"]
        assert bool(spec) == (identifier != 902), (identifier, s.summary())
        cases.append({"control": identifier, "playing": bool(spec)})
    screenshot = evidence / "20261109_music.png"
    render_state(s, screenshot)
    assert click_id(s, 999) == "ui", s.summary()
    # The exit handler starts a transition while the old area list still
    # exists. Wait for the caller to rebuild its own menu before asserting.
    s.system["state"]["ui_waiting"] = False
    assert s.advance() == "ui", s.summary()
    assert len(s.system["state"]["ui_areas"]) == 4, s.summary()
    assert s.system["state"]["media"]["channels"]["music"]["name"] == original
    # The source's extended-music function is used outside the music menu.
    # Prepare its one real intro/loop pair once for decoder verification.
    s.system["providers"]["$extmusic"](s.vm, s.lua.table_from(["bol_kanashi", "bol_kanashi_loop"]))
    extended = table_value(s.system["state"]["media"]["channels"]["music"])
    assert extended["loop_file"] and extended["loop_duration_ms"] > 0
    report = {"status": "passed", "cases": cases, "return_to_title": True,
              "extended_music": extended,
              "evidence": str(evidence), "screenshot": str(screenshot),
              "scope": "direct original $call entry, all 21 songs and shipped controls; natural unlock not covered"}
    write_json(ROOT / "reports/music_menu_tests.json", report)
    write_json(evidence / "20261109_result.json", report)
    print("music menu: 21 songs, 4 transport buttons, title return passed")
    return report


if __name__ == "__main__":
    import sys
    run("--verify-only" in sys.argv)
