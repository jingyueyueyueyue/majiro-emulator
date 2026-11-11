"""Replay early clicks with a legitimately generated profile and native pixels.

The first-frame workaround is disabled in this fixture. Success must come from
finishing source draw transactions, rather than displaying one frozen frame.
"""
from concurrent.futures import ProcessPoolExecutor, as_completed
from pathlib import Path
from unittest.mock import patch
import argparse
import json
import sys
import time

sys.path[:0] = [str(Path(__file__).resolve().parents[1] / "convert")]
from audit_startup_resources import Startup
from desktop_pixel_session import PixelSession
from lua_bridge import table_value
from port_common import ROOT, archive_dir, read_json, timestamp, write_json
from test_desktop_game_batch import files_for
from test_story_entry_regressions import run_story


def drive(game, files, mode, saved, folder, button, delay, legacy=False):
    class Session(PixelSession):
        def module(self, path):
            source = super().module(path)
            if legacy and path == "runtime/majiro_syscalls.lua":
                # Negative control: restore the exact faulty input gate,
                # keeping source scripts and every draw operation unchanged.
                source = source.replace("if input_boundary and ", "if ")
            return source
    session = Session(game, files, mode, folder, maximum=0, seconds=90)
    session.writes = dict(saved)
    read_text = Path.read_text

    def no_first_frame_workaround(path, *args, **kwargs):
        source = read_text(path, *args, **kwargs)
        if path == ROOT / "player/runtime/player_boot.lua":
            needle = "local bootstrap_frame=submitted_frames==0 and state.ui_waiting"
            assert needle in source
            source = source.replace(needle, "local bootstrap_frame=false")
        return source
    with patch.object(Path, "read_text", no_first_frame_workaround):
        boot = session.run()
    assert not boot["error"], boot["error"]
    state, vm = session.system["state"], session.vm
    trace, signals = [], []
    for name in ("$invalidate_disable", "$invalidate_enable"):
        original = session.system["providers"][name]

        def observe(owner, args, *rest, _name=name, _original=original):
            result = _original(owner, args, *rest)
            frame = owner["frame"](owner)
            trace.append({"name": _name, "clock": session.clock, "depth": state["invalidate_depth"],
                          "script": frame["script"]["name"], "offset": owner["current_offset"]})
            return result
        session.system["providers"][name] = observe
    original_signal = vm["signal"]

    def observe_signal(owner, kind, *rest):
        signals.append({"clock": session.clock, "kind": kind,
                        "source_wait": table_value(owner["waiting"]) if owner["waiting"] else None,
                        "invalidate_depth": state["invalidate_depth"]})
        return original_signal(owner, kind, *rest)
    vm["signal"] = observe_signal
    started, sent, settled = time.monotonic(), False, 0
    for _ in range(2400):
        event = {"x": 512, "y": 288, "buttons": 0, "held_buttons": 0}
        if button and not sent and session.clock >= delay:
            event["buttons"], sent = button, True
        session.lua.globals()["PLAYER_TICK"](session.clock, session.lua.table_from(event))
        session.clock += 50
        ready = state["ui_waiting"] or state["reading_wait"]
        settled = settled + 50 if ready else 0
        if settled >= 1500:
            break
        if vm["modal_dialog"] or vm["external_checker"]:
            break
        if time.monotonic() - started > 45:
            break
    stats = session.lua.globals()["PLAYER_STATUS"]()
    row = {"game": game["id"], "mode": mode, "button": button, "input_delay_ms": delay,
           "legacy_input_gate": legacy, "status": "running",
           "first_frame_workaround_disabled": True, "player_profile_used": False,
           "source_invalidation_depth": state["invalidate_depth"],
           "submitted_frames": stats["submitted_frames"], "native_frames": session.rendered_frames,
           "ui_script": state["active_ui_script"], "ui_waiting": bool(state["ui_waiting"]),
           "reading_wait": bool(state["reading_wait"]), "input_signals": signals,
           "invalidation_trace": trace, "seconds": round(time.monotonic() - started, 2)}
    row["frame"] = session.capture("legacy" if legacy else "settled")
    if not row["ui_waiting"] and not row["reading_wait"]:
        row.update(status="source_boundary", reason="no settled source input scene within budget")
    elif state["invalidate_depth"] != 0:
        row.update(status="failed", error="source drawing transaction leaked across early input")
    elif not row["submitted_frames"] or not row["frame"].get("nonblack"):
        row.update(status="failed", error="source input scene has no nonblack native frame")
    elif not legacy and any(item["source_wait"] is None for item in signals):
        row.update(status="failed", error="host instruction slice was treated as a source input boundary")
    else:
        row["status"] = "passed"
    write_json(session.folder / "report.json", row)
    return row


def worker(game, folder, negative_control=False):
    files, rows = files_for(game), []
    for mode in ("original", "online"):
        # Build a fresh profile by legal public title/story input. This
        # reproduces cold restarts without using a player's private saves.
        seed = Startup(game, files, mode, 2000000, 60)
        story = run_story(game, files, mode, 12, 60, session=seed)
        if story["status"] != "passed":
            rows.append({"game": game["id"], "mode": mode, "status": "profile_setup_boundary",
                         "reason": story["status"]})
            continue
        seed.system["saves"]["flush"](seed.vm)
        saved = dict(seed.writes)
        base = Path(folder) / game["id"].replace(" ", "_") / mode
        if negative_control and game["id"] == "kiraoto" and mode == "online":
            legacy = drive(game, files, mode, saved, base / "negative_control", 1, 0, True)
            assert legacy["status"] == "failed" and legacy["source_invalidation_depth"] == 1
            assert legacy["submitted_frames"] == 0, legacy
            write_json(base / "negative_control.json",
                       {"status": "passed", "expected_original_failure": legacy})
        for button, delay in ((0, 0), (1, 0), (2, 0), (1, 100)):
            label = "%d_%d" % (button, delay)
            try:
                row = drive(game, files, mode, saved, base / label, button, delay)
            except Exception as error:
                row = {"game": game["id"], "mode": mode, "button": button,
                       "input_delay_ms": delay, "status": "failed", "error": str(error)}
            rows.append(row)
    return rows


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--game", action="append")
    parser.add_argument("--jobs", type=int, default=4)
    parser.add_argument("--negative-control", action="store_true")
    args = parser.parse_args()
    folder = archive_dir("startup_input_transactions")
    games = [g for g in read_json(ROOT / "config/recovered_library.json")["games"]
             if not args.game or g["id"] in args.game]
    report = {"status": "running", "timestamp": timestamp(), "cases": [],
              "device_accessed": False, "real_native_pixels": True,
              "player_saves_used": False, "evidence": str(folder.relative_to(ROOT)),
              "scope": "cold startup from a genuine generated profile, early clicks, source draw balance; no bootstrap-frame workaround"}
    with ProcessPoolExecutor(max_workers=args.jobs) as pool:
        jobs = {pool.submit(worker, g, str(folder), args.negative_control): g["id"] for g in games}
        for future in as_completed(jobs):
            try:
                rows = future.result()
            except Exception as error:
                rows = [{"game": jobs[future], "status": "failed", "error": str(error)}]
            report["cases"].extend(rows)
            for row in rows:
                print(json.dumps({k: row.get(k) for k in
                                  ("game", "mode", "button", "input_delay_ms", "status",
                                   "source_invalidation_depth", "submitted_frames", "error")},
                                 ensure_ascii=True), flush=True)
            write_json(folder / "report.json", report)
            write_json(ROOT / "reports/startup_input_transactions.json", report)
    report.update(status="complete", timestamp=timestamp())
    write_json(folder / "report.json", report)
    write_json(ROOT / "reports/startup_input_transactions.json", report)
    return int(any(row["status"] == "failed" for row in report["cases"]))


if __name__ == "__main__":
    raise SystemExit(main())
