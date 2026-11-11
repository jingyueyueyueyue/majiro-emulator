"""Drag every visible original CONFIG track and check audition/persistence."""
from model_session import Session
from test_settings import click_id
from port_common import ROOT, archive_dir, write_json


def released_ticks(s):
    # Let every source slider poll the released state before the next action.
    # Merely advancing the clock and immediately pressing again skips that
    # transition for controls scheduled later in the source menu loop.
    for _ in range(3):
        s.clock["ms"] += 50
        s.event(buttons=0, held_buttons=0)
        assert s.vm["run"](s.vm, 30000) != "blocked", s.vm["problem"]


def drag(s, x, y, width, end=0.22):
    released_ticks(s)
    s.clock["ms"] += 500
    knobs = [v for v in s.system["state"]["sprites"].values()
             if v["width"] == 15 and v["height"] == 25 and
             abs(v["y"] + 12-y) <= 2 and x-10 <= v["x"] <= x+width+10]
    assert len(knobs) == 1, (x, y, len(knobs))
    before_x = knobs[0]["x"]
    # A maximum-value knob extends beyond the source's interactive track.
    # Start inside both the knob and track, just as an accepted pointer drag.
    sx, ex = min(x+width-1,max(x,knobs[0]["x"]+7)), x + round(width * end)
    y = knobs[0]["y"] + 12
    s.event(x=sx, y=y, buttons=1, held_buttons=1)
    assert s.vm["run"](s.vm, 30000) != "blocked", s.vm["problem"]
    for step in range(11):
        s.clock["ms"] += 20
        s.event(x=round(sx + (ex-sx)*step/10), y=y, held_buttons=1)
        assert s.vm["run"](s.vm, 30000) != "blocked", s.vm["problem"]
    s.event(x=ex, y=y, held_buttons=0)
    assert s.advance() == "ui", s.vm["problem"]
    released_ticks(s)
    after_x = knobs[0]["x"]
    assert after_x != before_x and abs(after_x + 7 - ex) <= 2, (before_x, after_x, ex)
    return {"before_x": before_x, "after_x": after_x, "pointer_end": ex}


def run():
    evidence = archive_dir("settings_sliders")
    s = Session(False, save_root=evidence / "saves")
    assert s.advance() == "ui" and s.click(3) == "ui"
    rows = []
    for field, y in (("mojispeed", 227), ("autospeed", 278), ("effectspeed", 329),
                     ("musicvolume", 427), ("voicevolume", 477), ("soundvolume", 528)):
        before = s.system["state"]["settings"][field]
        geometry = drag(s, 493, y, 511)
        rows.append({"setting": field, "before": before, "drag": geometry})
    # Audition must use each character's own slider multiplied by master voice.
    master = s.system["state"]["settings"]["voicevolume"]
    for identifier, x, y in ((512, 344, 580), (522, 769, 580),
                              (532, 344, 637), (542, 769, 637)):
        geometry = drag(s, x, y, 278)
        assert click_id(s, identifier) == "ui"
        voice = s.system["state"]["media"]["channels"]["voice"]
        assert voice and not voice["loop"], identifier
        assert 190 <= voice["channel_volume"] <= 250, (identifier, voice["channel_volume"])
        assert voice["volume"] == master * voice["channel_volume"] // 1000
        rows.append({"audition": identifier, "name": voice["name"],
                     "channel_volume": voice["channel_volume"], "effective_volume": voice["volume"],
                     "drag": geometry})
    assert click_id(s, 999) == "ui"
    # Original CONFIG commits the three timing values when the menu closes;
    # sound gains also update live for its audition controls.
    for row in rows[:6]:
        row["after"] = s.system["state"]["settings"][row["setting"]]
        if row["setting"] == "effectspeed":
            # Original CONFIG first computes the delay from its slider, then
            # explicitly overwrites the engine field with the effect toggle.
            # Verify that source behavior instead of inventing a new mapping.
            script = s.vm["scripts"]["config.mjo"]
            write = script["code"][script["positions"][0xbbfd]]
            assert write[2] == 0x1b0 and write[4] == 2302215223
            toggle = s.vm["globals"][0][4067595249]
            assert toggle in (0, 1)
            expected = 0 if toggle else 1000
            assert row["after"] == expected, row
            row["source_binary_effect_override"] = expected
        else:
            assert row["after"] != row["before"], row
    s.system["saves"]["flush"](s.vm)
    restarted = Session(False, save_root=evidence / "saves")
    assert restarted.advance() == "ui" and restarted.click(3) == "ui"
    for row in rows[:6]:
        assert restarted.system["state"]["settings"][row["setting"]] == row["after"], row
    for row in rows[6:]:
        assert click_id(restarted, row["audition"]) == "ui"
        voice = restarted.system["state"]["media"]["channels"]["voice"]
        assert voice["channel_volume"] == row["channel_volume"], row
    result = {"status": "passed", "cases": rows, "restart_retained_all_tracks": True,
              "evidence": str(evidence),
              "scope": "all ten original tracks and source knob movement; original binary effect commit, character audition gain and cold restart"}
    write_json(ROOT / "reports/settings_slider_tests.json", result)
    write_json(evidence / "20261109_result.json", result)
    print("CONFIG sliders:", len(rows), "tracks and cold restart passed")
    return result


if __name__ == "__main__":
    run()
