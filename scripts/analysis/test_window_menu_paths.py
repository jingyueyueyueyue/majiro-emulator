"""Exercise original load/save/config events from legitimate source story waits."""
from concurrent.futures import ProcessPoolExecutor, as_completed
from pathlib import Path
import argparse
import sys

sys.path[:0] = [str(Path(__file__).resolve().parents[1] / "convert")]
from port_common import ROOT, archive_dir, read_json, timestamp, write_json
from desktop_pixel_session import PixelSession
from lua_bridge import table_value
from test_desktop_game_batch import files_for, snapshot, restore, settle, tick
from test_story_entry_regressions import run_story


def worker(game, folder):
    rows = []
    for mode in ("original", "online"):
        session = PixelSession(game, files_for(game), mode,
                               Path(folder) / game["id"].replace(" ", "_") / mode)
        story = run_story(game, session.files, mode, 12, 90, session=session)
        if story["status"] != "passed":
            rows.append({"game": game["id"], "mode": mode, "status": "setup_boundary",
                         "reason": story["status"]})
            continue
        settle(session, 1000, 4000)
        snapshot(session, 700)
        state = session.system["state"]
        text, generation = state["console"]["text"], state["reading_generation"]
        for command, target in ((1, "loadsave.mjo"), (3, "loadsave.mjo"), (99, "config.mjo")):
            row = {"game": game["id"], "mode": mode, "command": command, "target": target}
            try:
                restore(session, 700)
                settle(session, 500, 2000)
                before_events = len(session.host_events)
                tick(session, {"window_command": command, "buttons": 0, "held_buttons": 0})
                settle(session, 1500, 6000)
                result = state["window_menu_result"]
                row.update(request_result=result, active_ui_script=state["active_ui_script"])
                if result in ("disabled", "unsupported"):
                    row["status"] = "source_inactive"
                elif result != "queued":
                    raise AssertionError("window command returned no explicit outcome")
                elif state["active_ui_script"] != target:
                    # Native setting dialogs use their own host contract. A
                    # source callback returning without custom areas is not a
                    # successful custom-menu interaction.
                    native_dialog=any(e.get("kind")=="native_dialog_cancel_fixture"
                                      for e in session.host_events[before_events:])
                    row.update(status="native_dialog_fixture"if native_dialog else"source_menu_boundary",
                               source_draw_depth=state["invalidate_depth"],
                               source_modal_depth=len(state["modal_stack"]or[]),
                               event_owners=[{"owner":hook["owner"],
                                   "waiting":table_value(session.vm["threads"][hook["owner"]]["waiting"]),
                                   "finished":session.vm["threads"][hook["owner"]]["finished"]}
                                   for hook in state["events"][{1:"LOADMENU",3:"SAVEMENU",99:"CONFIGMENU"}[command]].values()],
                               native_dialog_fixture_observed=native_dialog)
                else:
                    row["frame"] = session.capture("window_%d" % command)
                    assert row["frame"]["nonblack"], "window menu has no composed pixels"
                    for _ in range(2):
                        tick(session, {"buttons": 2, "held_buttons": 2}, 500)
                        tick(session, {"buttons": 0, "held_buttons": 0}, 50)
                        settle(session, 1000, 4000)
                        if state["reading_wait"] and state["active_ui_script"] != target:
                            break
                    row["returned_to_story"] = bool(state["reading_wait"])
                    row["same_story_text"] = state["console"]["text"] == text
                    row["same_reading_generation"] = state["reading_generation"] == generation
                    assert all(row[k] for k in ("returned_to_story", "same_story_text",
                                                "same_reading_generation")), "window menu did not return to the suspended story"
                    row["status"] = "passed"
            except Exception as error:
                row.update(status="failed", error=str(error))
                row["failure_frame"] = session.capture("window_%d_failure" % command)
            rows.append(row)
    return rows


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--game", action="append")
    parser.add_argument("--jobs", type=int, default=4)
    args = parser.parse_args()
    folder = archive_dir("window_menu_paths")
    report = {"timestamp": timestamp(), "status": "running", "cases": [],
              "device_accessed": False, "player_saves_used": False,
              "scope": "12 legal story pages, real original window menu callbacks, native pixels and unchanged-story return",
              "evidence": str(folder.relative_to(ROOT))}
    games = [g for g in read_json(ROOT / "config/recovered_library.json")["games"]
             if not args.game or g["id"] in args.game]
    with ProcessPoolExecutor(max_workers=args.jobs) as pool:
        jobs = {pool.submit(worker, g, str(folder)): g["id"] for g in games}
        for future in as_completed(jobs):
            try:
                rows = future.result()
            except Exception as error:
                rows = [{"game": jobs[future], "status": "failed", "error": str(error)}]
            report["cases"].extend(rows)
            write_json(folder / "report.json", report)
            write_json(ROOT / "reports/window_menu_paths.json", report)
            print(jobs[future], [(r.get("mode"), r.get("command"), r["status"], r.get("error")) for r in rows],
                  flush=True)
    report.update(status="complete", timestamp=timestamp())
    write_json(folder / "report.json", report)
    write_json(ROOT / "reports/window_menu_paths.json", report)
    return int(any(r["status"] == "failed" for r in report["cases"]))


if __name__ == "__main__":
    raise SystemExit(main())
