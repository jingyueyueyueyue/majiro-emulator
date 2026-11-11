"""Public source quick-save/load and playback gestures in isolated real sessions."""
from concurrent.futures import ProcessPoolExecutor, as_completed
from pathlib import Path
import argparse
from collections import Counter
import json
import msvcrt
import os
import sys
import traceback

ROOT = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(ROOT / "scripts" / n) for n in ("analysis", "convert", "build")]
from port_common import archive_dir, read_json, timestamp, write_json, sha256
from desktop_pixel_session import PixelSession
from test_desktop_game_batch import files_for, tick, restore
from test_story_entry_regressions import run_story
from run_background_compatibility import source_identity
from lua_bridge import table_value
from source_menu_navigation import visible

QUICK = "\u30af\u30a4\u30c3\u30af"


class SourcePermissionBoundary(Exception):
    pass


class ControlsSession(PixelSession):
    def __init__(self, *args, **kwargs):
        self.calls, self.all_calls, self.inputs, self.native_dialogs, self.texts = [], [], [], [], []
        self.fixture_operation = False
        self.quick_confirms = 0
        self.public_quick_request = False
        self.mode_stops = []
        super().__init__(*args, **kwargs)

    def graphics(self, request):
        if request.startswith(("glyphs ", "text ")):
            self.texts.append(request.split("\n")[-1])
            self.texts = self.texts[-100:]
        return super().graphics(request)

    def dialog(self, action, token, *args):
        if action == "begin":
            flags, message, caption = args
            self.fixture_dialogs = getattr(self, "fixture_dialogs", {})
            self.fixture_dialogs[token] = (flags, message)
            self.native_dialogs.append({"clock": self.clock, "flags": flags,
                                        "message": message, "caption": caption})
            return "pending"
        if action == "cancel":
            self.fixture_dialogs.pop(token, None)
            return "OK"
        flags, message = self.fixture_dialogs.pop(token)
        if flags >= 65536:
            return "data "
        buttons = flags % 16
        if buttons == 4:
            return "result " + ("6" if QUICK in message else "7")
        return "result " + ("2" if buttons in (1, 5) else "1")

    def observe(self, name, vm, args):
        if self.fixture_operation:
            return
        state = self.system["state"]
        frame = vm["frame"](vm)
        record = {
            "api": name, "args": [table_value(args[i]) for i in range(1, len(args) + 1)],
            "clock": self.clock,
            "script": frame["script"]["name"],
            "source_sha256": frame["script"]["source_sha256"],
            "offset": vm["current_offset"], "thread": vm["thread_id"],
            "generation": state["reading_generation"],
            "source_capture_enabled": (state["settings"]["save"] is not False
                                       and state["settings"]["save_menu"] is not False),
        }
        self.calls.append(record)
        self.all_calls.append(record)

    def install_observers(self):
        self.lua.eval("""function(system,observe)
          for _,name in ipairs{'$save_point','$do_save','$do_load','$set_auto_mode','$set_fast_mode',
              '$set_fast_mode_force','$set_fast_mode_ar'}do
            local api=name
            local original=assert(system.providers[name])
            local wrapper=function(vm,args,...)
              observe(api,vm,args)
              return original(vm,args,...)
            end
            local aliases={}
            for key,fn in pairs(system.providers)do
              if fn==original then aliases[#aliases+1]=key end
            end
            for _,key in ipairs(aliases)do system.providers[key]=wrapper end
          end
        end""")(self.system, self.observe)
        def stopped(vm, result):
            state = self.system["state"]
            frame = vm["frame"](vm)
            self.mode_stops.append({
                "clock": self.clock, "result": result,
                "current_text_read": state["current_text_read"],
                "reading_wait": state["reading_wait"],
                "reading_generation": state["reading_generation"],
                "reading_positions": table_value(state["reading_positions"]),
                "script": frame["script"]["name"], "offset": vm["current_offset"],
                "source_sha256": frame["script"]["source_sha256"],
            })
        self.lua.eval("""function(system,stopped)
          local original=assert(system.providers['$is_fast_mode'])
          local wrapper=function(vm,args,...)
            local active=system.state.settings.fast_read_mode
            local value=original(vm,args,...)
            if active and not system.state.settings.fast_read_mode then stopped(vm,value)end
            return value
          end
          local aliases={}
          for key,fn in pairs(system.providers)do if fn==original then aliases[#aliases+1]=key end end
          for _,key in ipairs(aliases)do system.providers[key]=wrapper end
        end""")(self.system, stopped)

    def checkpoint(self, slot):
        settings = self.system["state"]["settings"]
        if settings["save"] is False or settings["save_menu"] is False:
            raise SourcePermissionBoundary("original source disables checkpoint/menu capture here")
        if not self.system["saves"]["stats"]()["checkpoint"]:
            raise SourcePermissionBoundary("original source has no captured checkpoint")
        self.fixture_operation = True
        try:
            # Save the last checkpoint taken by the actual source. Never
            # replace its capture point just to reset a test input.
            self.system["providers"]["$do_save"](self.vm, self.lua.table_from([slot]))
        finally:
            self.fixture_operation = False

    def reset_to(self, slot):
        self.fixture_operation = True
        try:
            restore(self, slot)
        finally:
            self.fixture_operation = False
        self.idle(1500)
        self.calls.clear()
        self.texts.clear()
        self.mode_stops.clear()

    def feed(self, event, delta=50):
        self.inputs.append({"clock": self.clock + delta, "delta": delta, "event": event})
        tick(self, event, delta)

    def idle(self, duration):
        self.inputs.append({"clock": self.clock, "idle_ms": duration, "cadence_ms": 50})
        for _ in range((duration + 49) // 50):
            tick(self, {"buttons": 0, "held_buttons": 0})

    def areas(self):
        return [a for a in self.system["state"]["ui_areas"].values() if visible(self, a)]

    def click_area(self, route):
        area = next((a for a in self.areas()
                     if a["script"] == route["script"] and a["args"][1] == route["id"]), None)
        if area is None:
            return False
        if route["script"] == "console.mjo" and route["id"] in (8, 9):
            self.public_quick_request = True
        args = area["args"]
        point = {"x": args[2] + args[4] / 2 + .25, "y": args[3] + args[5] / 2 + .625}
        self.feed(point | {"buttons": 0, "held_buttons": 0}, 400)
        self.feed(point | {"buttons": 1, "held_buttons": 1}, 50)
        self.feed(point | {"buttons": 0, "held_buttons": 0}, 50)
        self.idle(2500)
        self.confirm_quick_question()
        self.dismiss_quick_notice()
        return True

    def confirm_quick_question(self):
        areas = self.areas()
        prompt_seen = any(QUICK in text and ("\u3088\u308d\u3057\u3044" in text or "?" in text or "\uff1f" in text)
                          for text in self.texts[-30:])
        source_checkbox_prompt = (self.public_quick_request
            and self.system["state"]["active_ui_script"] == "message_box.mjo"
            and {a["args"][1] for a in areas} == {0, 1, 2})
        if not (self.quick_confirms < 2 and (prompt_seen or source_checkbox_prompt)
                and 0 < len(areas) <= 3):
            return
        yes = next((a for a in areas if a["args"][1] == 1
                    and a["script"] in ("buttonmenu.mjo", "menu.mjo", "message_box.mjo")), None)
        if yes is not None:
            # An actual public yes area, only for an observed quick-action
            # question. This does not inject a dialog result or alter a PC.
            self.quick_confirms += 1
            self.texts.clear()
            self.click_area({"script": yes["script"], "id": 1})

    def dismiss_quick_notice(self):
        waiting = self.vm["waiting"]
        if (waiting and waiting["kind"] == "input"
                and any(QUICK in text and "\u3057\u307e\u3057\u305f" in text
                        for text in self.texts[-100:])):
            self.capture("quick_saved_notification_before_dismiss")
            width, height = self.lua.globals()["PLAYER_SIZE"]()
            self.feed({"x": width / 2, "y": height / 2,
                       "buttons": 1, "held_buttons": 1}, 400)
            self.feed({"buttons": 0, "held_buttons": 0}, 50)
            self.idle(1000)

    def diagnostics(self, label):
        state = self.system["state"]
        return {
            "clock": self.clock, "vm_status": self.vm["status"],
            "console_visible": bool(state["console"]["visible"]),
            "source_invalidation_depth": state["invalidate_depth"],
            "present_requested": state["present_requested"],
            "present_completed": state["present_completed"],
            "native_rendered_frames": self.rendered_frames,
            "waiting": table_value(self.vm["waiting"]),
            "reading_wait": bool(state["reading_wait"]),
            "ui_waiting": bool(state["ui_waiting"]),
            "active_ui_script": state["active_ui_script"],
            "generation": state["reading_generation"],
            "console_text": state["console"]["text"],
            "current_text_read": state["current_text_read"],
            "areas": [step_for(a) for a in self.areas()],
            "frames": [{"script": f["script"]["name"],
                        "offset": f["script"]["code"][f["index"]][1]}
                       for f in self.vm["frames"].values()],
            "recent_drawn_text": self.texts[-60:],
            "screenshot": self.capture(label),
        }

    def apply_route(self, route):
        self.quick_confirms = 0
        self.public_quick_request = False
        for step in route:
            if step["kind"] == "right":
                self.feed({"x": -1000, "y": -1000, "buttons": 2, "held_buttons": 2}, 400)
                self.feed({"buttons": 0, "held_buttons": 0}, 50)
                self.idle(1200)
            elif not self.click_area(step):
                return False
        return True


def step_for(area):
    return {"kind": "area", "script": area["script"], "id": area["args"][1],
            "rectangle": [area["args"][i] for i in range(2, 6)]}


def targets(session):
    result = set()
    for call in session.calls:
        if call["api"] in ("$do_save", "$do_load"):
            args = call["args"]
            if args == [9999]:
                result.add("quick_save" if call["api"] == "$do_save" else "quick_load")
        elif call["api"] == "$set_auto_mode":
            result.add("auto")
        elif call["api"].startswith("$set_fast_mode"):
            result.add("fast")
    return result


def discover(session):
    session.checkpoint(703)
    roots = [step_for(a) for a in session.areas() if a["script"] == "console.mjo"
             and a["args"][1] in (2, 3, 8, 9)]
    openers = [[step_for(a)] for a in session.areas() if a["script"] == "console.mjo"
               and a["args"][1] == 4]
    openers.append([{"kind": "right"}])
    found, attempts = {}, []
    for route in [[root] for root in roots]:
        session.reset_to(703)
        available = session.apply_route(route)
        reached = targets(session)
        attempts.append({"route": route, "available": available, "targets": sorted(reached),
                         "source_calls": list(session.calls),
                         "diagnostics": session.diagnostics("discover_" + str(len(attempts)))})
        for name in reached:
            found.setdefault(name, route)
    for opener in openers:
        if len(found) == 4:
            break
        session.reset_to(703)
        if not session.apply_route(opener):
            continue
        state = session.system["state"]
        if state["active_ui_script"] not in ("menu.mjo", "pausemenu.mjo"):
            attempts.append({"route": opener, "status": "source_did_not_open_popup"})
            continue
        children = [step_for(a) for a in session.areas()
                    if a["script"] in ("menu.mjo", "pausemenu.mjo")
                    and a["args"][1] not in (0, 1)]
        for child in children[:12]:
            session.reset_to(703)
            route = opener + [child]
            available = session.apply_route(route)
            reached = targets(session)
            attempts.append({"route": route, "available": available, "targets": sorted(reached),
                             "source_calls": list(session.calls),
                             "diagnostics": session.diagnostics("discover_" + str(len(attempts)))})
            for name in reached:
                found.setdefault(name, route)
    return found, attempts


def advance_pages(session, count):
    state = session.system["state"]
    initial = state["reading_generation"]
    for _ in range(80):
        if state["reading_generation"] >= initial + count:
            return
        waiting = session.vm["waiting"]
        if state["reading_wait"] or (waiting and waiting["kind"] == "input"
                                    and not state["ui_waiting"]):
            width, height = session.lua.globals()["PLAYER_SIZE"]()
            session.feed({"x": width / 2, "y": height / 2, "buttons": 1, "held_buttons": 1}, 400)
            session.feed({"buttons": 0, "held_buttons": 0}, 50)
        session.idle(500)
    raise AssertionError("public advance did not reach the requested next pages")


def worker(game, folder, pages, seconds, modes):
    results = []
    for mode in modes:
        session = ControlsSession(game, files_for(game), mode,
                                  Path(folder) / game["id"].replace(" ", "_") / mode)
        row = {"game": game["id"], "mode": mode, "cases": [], "status": "running"}
        try:
            story = run_story(game, session.files, mode, pages, seconds, session=session)
            row["setup"] = story
            if story["status"] != "passed":
                row.update(status="setup_boundary")
                results.append(row)
                continue
            session.idle(1000)
            session.install_observers()
            row["original_source_savepoint"] = table_value(session.system["state"]["savepoint"])
            row["source_inactive_controls"] = [step_for(a) for a in
                session.system["state"]["ui_areas"].values()
                if a["script"] == "console.mjo" and a["args"][1] in (2, 3, 4, 8, 9)
                and not visible(session, a)]
            found, row["discovery"] = discover(session)
            row["routes"] = found
            state = session.system["state"]
            for name in ("auto", "fast"):
                case = {"target": name, "status": "source_route_boundary"}
                if name in found:
                    session.reset_to(703)
                    initial = state["reading_generation"]
                    assert session.apply_route(found[name]), "discovered playback route disappeared"
                    assert name in targets(session), "public input did not invoke the playback action"
                    invoked = {c["api"] for c in session.calls}
                    read_only = (name == "fast" and "$set_fast_mode_force" not in invoked and (
                        "$set_fast_mode_ar" in invoked or
                        (state["settings"]["fast_mode_config"] or 0) != 0))
                    for _ in range(60):
                        if state["reading_generation"] >= initial + 2:
                            break
                        if name == "fast" and read_only and not state["settings"]["fast_read_mode"]:
                            break
                        session.idle(500)
                    advanced = state["reading_generation"] - initial
                    if name == "fast" and read_only and advanced < 2:
                        assert session.mode_stops, "read-only skip stopped without an observed mode-query transition"
                        assert all(not stop["current_text_read"] and stop["result"] == 0
                                   for stop in session.mode_stops), "skip query disabled itself on already-read text"
                        assert not state["settings"]["fast_read_mode"], "read-only skip remained enabled on unread text"
                        case["status"] = "passed_source_unread_stop"
                        case["stopped_on_actual_unread_text"] = True
                        case["mode_stop_queries"] = list(session.mode_stops)
                    else:
                        assert advanced >= 2, "playback action did not advance two pages without clicks"
                        case["status"] = "passed"
                    case.update(advanced_pages=advanced, observed_read_only_skip=read_only)
                    session.feed({"x": -1000, "y": -1000, "buttons": 2, "held_buttons": 2}, 50)
                    session.feed({"buttons": 0, "held_buttons": 0}, 50)
                    session.idle(1000)
                    assert not any(state["settings"][k] for k in
                                   ("auto_mode", "fast_mode", "fast_read_mode")), "right input did not stop playback"
                    case["stopped_by_public_right"] = True
                    case["frame"] = session.capture(name + "_stopped")
                    assert case["frame"]["nonblack"], "public playback stop produced an all-black native frame"
                    case["source_calls"] = list(session.calls)
                row["cases"].append(case)
            case = {"target": "quick_roundtrip", "status": "source_route_boundary"}
            if "quick_save" in found and "quick_load" in found:
                session.reset_to(703)
                advance_pages(session, 2)
                captures = [call for call in session.calls if call["api"] == "$save_point"
                            and call["source_capture_enabled"]]
                assert captures, "no natural source checkpoint was taken before the quick-save action"
                case["natural_source_captures"] = captures
                text, generation = state["console"]["text"], state["reading_generation"]
                assert session.apply_route(found["quick_save"])
                assert "quick_save" in targets(session)
                assert state["save_catalog"][session.system["saves"]["key"](9999)] is not None
                advance_pages(session, 2)
                assert state["reading_generation"] >= generation + 2, "story did not advance after source quick save"
                case["advanced_generation"] = state["reading_generation"]
                session.calls.clear()
                assert session.apply_route(found["quick_load"])
                assert "quick_load" in targets(session)
                session.idle(1500)
                assert state["console"]["text"] == text, "quick load restored different dialogue"
                assert state["reading_generation"] == generation, "quick load restored different reading generation"
                case.update(status="passed", same_text=True, same_reading_generation=True,
                            restored_generation=generation, frame=session.capture("quick_loaded"))
                assert case["frame"]["nonblack"]
                case["source_calls"] = list(session.calls)
            row["cases"].append(case)
            if case["status"] == "passed" and "fast" in found:
                # The two pages naturally read after quick save remain read
                # after loading. Exercise read-only skip over that real read
                # history; never set read/unlock flags.
                expected_unread_limit = case["advanced_generation"]
                replay = {"target": "fast_previously_read_pages", "status": "running"}
                start_generation = state["reading_generation"]
                start_text = state["console"]["text"]
                row["cases"].append(replay)
                session.calls.clear()
                assert session.apply_route(found["fast"])
                assert "fast" in targets(session), "read-page skip route was not executed"
                for _ in range(100):
                    if state["reading_generation"] >= start_generation + 2:
                        break
                    session.idle(200)
                replay.update(start_generation=start_generation, start_text=start_text,
                              advanced_pages=state["reading_generation"] - start_generation,
                              previously_read_limit=expected_unread_limit)
                assert replay["advanced_pages"] >= 2, "skip did not traverse the two naturally read pages"
                invoked = {c["api"] for c in session.calls}
                read_only = ("$set_fast_mode_force" not in invoked and (
                    "$set_fast_mode_ar" in invoked or
                    (state["settings"]["fast_mode_config"] or 0) != 0))
                replay["read_only"] = read_only
                if read_only:
                    session.idle(4000)
                    assert not state["settings"]["fast_read_mode"], "read-only skip remained active at new unread text"
                    assert session.mode_stops, "read-only replay stopped without a captured query"
                    assert all(not stop["current_text_read"] and stop["result"] == 0
                               for stop in session.mode_stops), "read-only replay stopped on a read-text query"
                    assert state["reading_generation"] <= expected_unread_limit + 1, "read-only skip crossed the unread page"
                    replay["stopped_at_unread"] = True
                    replay["mode_stop_queries"] = list(session.mode_stops)
                session.feed({"x": -1000, "y": -1000, "buttons": 2, "held_buttons": 2}, 50)
                session.feed({"buttons": 0, "held_buttons": 0}, 50)
                session.idle(1000)
                assert not any(state["settings"][k] for k in
                               ("auto_mode", "fast_mode", "fast_read_mode"))
                replay.update(status="passed", source_calls=list(session.calls),
                              diagnostics=session.diagnostics("read_only_fast_replay"))
            row["status"] = ("passed" if all(c["status"] in ("passed", "passed_source_unread_stop") for c in row["cases"])
                             else "coverage_incomplete")
        except SourcePermissionBoundary as error:
            row.update(status="source_permission_boundary", reason=str(error))
        except Exception as error:
            row.update(status="failed", error=str(error), error_type=type(error).__name__,
                       traceback=traceback.format_exc())
            if session.system:
                row["failure_frame"] = session.capture("failure")
                row["failure_state"] = session.diagnostics("failure_details")
                row["problem"] = table_value(session.vm["problem"])
                row["trace"] = table_value(session.vm["trace"])
        row["native_frames"] = session.rendered_frames
        write_json(session.folder / "source_calls.json", session.all_calls)
        write_json(session.folder / "inputs.json", session.inputs)
        write_json(session.folder / "native_dialogs.json", session.native_dialogs)
        write_json(session.folder / "read_skip_stop_queries.json", session.mode_stops)
        write_json(session.folder / "report.json", row)
        results.append(row)
    return results


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--game", action="append")
    parser.add_argument("--mode", action="append", choices=("original", "online"))
    parser.add_argument("--pages", type=int, default=12)
    parser.add_argument("--seconds", type=int, default=240)
    parser.add_argument("--jobs", type=int, default=4)
    args = parser.parse_args()
    with (ROOT / "reports/background_compatibility.lock").open("r+b") as lock:
        parent_owned = False
        try:
            msvcrt.locking(lock.fileno(), msvcrt.LK_NBLCK, 1)
        except OSError:
            # The official queue already owns this byte while running its
            # direct child. Accept only that exact active parent/stage; a
            # standalone invocation must still reject overlapping work.
            owner = os.environ.get("MAJIRO_COMPATIBILITY_QUEUE_PID", "")
            state = read_json(ROOT / "reports/background_compatibility_state.json")
            parent_owned = (owner.isdecimal() and int(owner) == os.getppid()
                and state.get("pid") == int(owner) and state.get("status") == "running"
                and state.get("current_stage") == "source_playback_controls")
            if not parent_owned:
                print("Existing compatibility work owns the lock; no overlapping source-controls batch started.")
                return 3
        try:
            folder = archive_dir("source_playback_controls")
            before = source_identity()
            candidate_hash = sha256(Path(__file__))
            (folder / "candidate_source.py").write_bytes(Path(__file__).read_bytes())
            games = [g for g in read_json(ROOT / "config/recovered_library.json")["games"]
                     if not args.game or g["id"] in args.game]
            report = {
                "timestamp": timestamp(), "status": "running", "pid": os.getpid(),
                "source_identity": before, "evidence": str(folder.relative_to(ROOT)), "cases": [],
                "candidate_sha256": candidate_hash,
                "setup_pages": args.pages, "setup_seconds_per_mode": args.seconds,
                "device_accessed": False, "player_saves_used": False, "real_api_called": False,
                "fixture_candidate_integrated": True,
                "scope": "legal title-to-story, public source controls, exact quick restoration and automatic progression",
                "fixture_boundaries": ["source's existing checkpoint saved to an isolated reset slot; no manual checkpoint capture or permission changes",
                                      "native quick-action confirmations and real source yes areas are explicitly approved",
                                      "audio/movie transport and online delay use local metadata fixtures"],
            }
            def publish():
                report["updated"] = timestamp()
                write_json(folder / "report.json", report)
                write_json(ROOT / "reports/source_playback_controls.json", report)
            publish()
            with ProcessPoolExecutor(max_workers=args.jobs) as pool:
                jobs = {pool.submit(worker, g, str(folder), args.pages, args.seconds,
                                    args.mode or ("original", "online")): g["id"] for g in games}
                for future in as_completed(jobs):
                    try:
                        rows = future.result()
                    except Exception as error:
                        rows = [{"game": jobs[future], "status": "failed", "error": str(error)}]
                    report["cases"].extend(rows)
                    publish()
                    print([(r["game"], r.get("mode"), r["status"], r.get("error")) for r in rows], flush=True)
            counts = dict(Counter(r["status"] for r in report["cases"]))
            report.update(counts=counts, source_unchanged=(source_identity() == before
                          and sha256(Path(__file__)) == candidate_hash),
                          status="failed" if counts.get("failed") else
                          "coverage_incomplete" if any(status not in ("passed", "failed") for status in counts)
                          else "passed")
            publish()
            return 1 if report["status"] == "failed" or not report["source_unchanged"] else 2 if report["status"] != "passed" else 0
        finally:
            if not parent_owned:
                lock.seek(0)
                msvcrt.locking(lock.fileno(), msvcrt.LK_UNLCK, 1)


if __name__ == "__main__":
    raise SystemExit(main())
