"""Regress actual chapter right-click scripts in original and local text modes."""
from __future__ import annotations

import argparse
from pathlib import Path
import sys
import time

sys.path[:0] = [str(Path(__file__).resolve().parents[1] / directory)
                for directory in ("analysis", "build", "convert")]
from audit_startup_resources import Startup
from deploy_player_android import games, payload
from port_common import ROOT, archive_dir, sha256, timestamp, write_json

CHAPTERS = {
    "RURINOKASANE": ("sub_title_yura.mjo", "sub_title_iichiko.mjo",
                    "sub_title_ruri_akarui.mjo", "sub_title_ruri_sibou.mjo"),
    "kiraoto": ("sub_title.mjo",),
}


def summary(session):
    state, vm = session.system["state"], session.vm
    return {
        "root": vm["frames"][1]["script"]["name"],
        "active": state["active_ui_script"],
        "chapter_menu": bool(state["chapter_menu_active"]),
        "area_ids": [area["args"][1] for area in state["ui_areas"].values()],
        "save": state["settings"]["save"],
        "save_menu": state["settings"]["save_menu"],
    }


def drive(session, event=None):
    state = session.system["state"]
    state["ui_waiting"] = False
    began = time.monotonic()
    for tick in range(2000):
        session.clock += 50
        session.lua.globals()["PLAYER_TICK"](
            session.clock, session.lua.table_from(event if tick == 0 and event else {}))
        assert session.vm["status"] != "blocked", "chapter navigation blocked"
        if tick > 5 and state["ui_waiting"] and not state["reading_wait"]:
            return summary(session)
        if time.monotonic() - began > 30:
            break
    raise AssertionError("chapter navigation did not reach a source UI wait")


def click_id(session, identifier):
    area = next(area["args"] for area in session.system["state"]["ui_areas"].values()
                if area["args"][1] == identifier)
    pointer = {"x": area[2] + area[4] // 2, "y": area[3] + area[5] // 2}
    drive(session, pointer)
    return drive(session, dict(pointer, buttons=1))


def run_case(game, files, mode, chapter, input_source=None):
    session = Startup(game, files, mode, 2000000, 40)
    if input_source is not None:
        original_module = session.module
        session.module = lambda name: (input_source if name == "runtime/majiro_input.lua"
                                       else original_module(name))
    boot = session.run()
    assert not boot["error"], boot["error"]
    assert boot["stop"] == "source_ui_wait", boot["stop"]
    observations = []
    for cycle in range(2):
        session.system["providers"]["$exec"](session.vm, session.lua.table_from([chapter]))
        entered = drive(session)
        assert entered["root"] == chapter and entered["active"] == "buttonmenu.mjo", entered
        right = drive(session, {"x": 100000, "y": 100000, "buttons": 2})
        row = {"cycle": cycle, "chapter": entered, "right": right}
        if game["id"] == "RURINOKASANE":
            assert right["root"] == chapter and right["active"] == "menu.mjo", right
            assert right["chapter_menu"] and not right["save"] and not right["save_menu"], right
            resumed = click_id(session, 4)
            assert resumed == entered, (entered, resumed)
            row["resumed"] = resumed
            returned = click_id(session, 99)
        else:
            returned = right
        assert returned["root"] == "start.mjo" and not returned["chapter_menu"], returned
        row["title"] = returned
        observations.append(row)
    return observations


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--game", choices=tuple(CHAPTERS))
    parser.add_argument("--mode", choices=("original", "local"))
    parser.add_argument("--input-runtime", type=Path, help="optional historical input source for reproduction")
    args = parser.parse_args()
    folder = archive_dir("chapter_menu_dispatch")
    files, roots = payload(folder)
    samples = {game["id"]: game for game in games()}
    before = sha256(ROOT / "player/runtime/majiro_input.lua")
    report = {
        "timestamp": timestamp(), "status": "running", "cases": [],
        "runtime_sha256": before, "device_accessed": False, "player_saves_used": False,
        "translation_content_modified": False, "full_routes_verified": False,
        "scope": "production player_boot/VM/input with actual chapter/menu scripts and decoded resources; graphics metadata host",
        "evidence": str(folder.relative_to(ROOT)),
    }
    input_source = args.input_runtime.read_text(encoding="utf8") if args.input_runtime else None
    for title, chapters in CHAPTERS.items():
        if args.game and title != args.game:
            continue
        prefix = next(root["directory"] for root in roots if root["id"] == title) + "/"
        source = {target[len(prefix):]: path for path, target in files if target.startswith(prefix)}
        for mode in (args.mode,) if args.mode else ("original", "local"):
            for chapter in chapters:
                row = {"game": title, "mode": mode, "chapter": chapter}
                try:
                    row.update(status="passed", navigation=run_case(
                        samples[title], source, mode, chapter, input_source))
                except Exception as error:
                    row.update(status="failed", error=str(error))
                report["cases"].append(row)
                print(title, mode, chapter, row["status"], row.get("error", "")[:300], flush=True)
                write_json(folder / "report.json", report)
    report.update(status="passed" if all(row["status"] == "passed" for row in report["cases"]) else "failed",
                  source_changed=sha256(ROOT / "player/runtime/majiro_input.lua") != before)
    assert not report["source_changed"], "input source changed during regression"
    write_json(folder / "report.json", report)
    if not args.input_runtime:
        write_json(ROOT / "reports/chapter_menu_dispatch_tests.json", report)
    return int(report["status"] != "passed")


if __name__ == "__main__":
    raise SystemExit(main())
