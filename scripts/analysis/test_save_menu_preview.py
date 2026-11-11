"""Inspect thumbnails inside the actual save menu, including the selected slot."""
from concurrent.futures import ProcessPoolExecutor, as_completed
from pathlib import Path
import argparse
import json
import sys

from PIL import Image

sys.path[:0] = [str(Path(__file__).resolve().parents[1] / "convert")]
from port_common import ROOT, archive_dir, read_json, timestamp, write_json
from desktop_pixel_session import PixelSession
from test_desktop_game_batch import click, files_for, restore, settle, snapshot, tick
from test_story_entry_regressions import run_story
from source_menu_navigation import open_story_save


def current_preview_contract(vm):
    """Read direct source guards controlling the current-preview native call."""
    script = vm["scripts"]["loadsave.mjo"]
    code, guards = list(script["code"].values()), []
    copy_calls=0
    for i, row in enumerate(code):
        if row[2] not in (0x834, 0x835) or row[3] != 0x89ce8c53:
            continue
        copy_calls+=1
        for j in range(max(1, i - 28), i):
            branch, load = code[j], code[j - 1]
            if branch[2] != 0x82e or branch[3] <= row[1] or load[2] != 0x802:
                continue
            flags, hash_, offset = load[3], load[4], load[5]
            # Only pure integer globals immediately consumed by brfalse.
            if flags % 8 or (flags // 32) % 8 > 1 or (flags // 256) % 8:
                continue
            bank, key, _ = vm["variable"](vm, flags, hash_, offset)
            raw = bank[key]
            if raw is None:
                continue
            invert = (flags // 8) % 4
            if invert == 2:
                value = int(raw == 0)
            elif invert == 0:
                value = raw
            elif invert == 1:
                value = -raw
            else:
                continue
            guards.append({"offset": branch[1], "flags": flags, "hash": hash_,
                           "raw_value": raw, "condition": value})
    return {"source_sha256": script["source_sha256"], "guards": guards,
            "source_current_copy_calls":copy_calls,
            "disabled": copy_calls==0 or any(g["condition"] == 0 for g in guards)}


def worker(game, folder, pages=12):
    session = PixelSession(game, files_for(game), "original",
                           Path(folder) / game["id"].replace(" ", "_"))
    row = {"game": game["id"], "status": "running", "requested_scene_pages": pages}
    try:
        story = run_story(game, session.files, "original", pages, max(90, pages * .6), session=session)
        if story["status"] != "passed":
            return row | {"status": "setup_boundary", "reason": story["status"]}
        settle(session, 1000, 4000)
        state, providers, vm = session.system["state"], session.system["providers"], session.vm
        # Snapshot the story before opening any popup, so a later restore
        # recreates its genuine picture rather than a menu-covered scene.
        snapshot(session, 700)
        row["source_path"] = open_story_save(session)
        # Learn the source's own thumbnail size before creating the saved
        # fixture. Games use different sizes; forcing 250x141 clips older UIs.
        settle(session, 1000, 4000)
        if state["active_ui_script"] != "loadsave.mjo":
            return row | {"status": "native_dialog_fixture"if "native_dialog_fixture"in row["source_path"]else"different_source_menu",
                          "active_ui_script": state["active_ui_script"]}
        thumb = state["current_thumbnail"]
        if not thumb:
            return row | {"status": "source_thumbnail_not_created"}
        size = [state["pages"][thumb]["width"], state["pages"][thumb]["height"]]
        restore(session, 700)
        settle(session, 500, 2000)
        providers[0x9143dc9e](vm, session.lua.table_from(size))
        snapshot(session, 101)
        copied = []
        observed = set()

        def verify_copy(item):
            signature = (item["kind"], item.get("slot"), item["page"], item["x"], item["y"])
            if signature in observed:
                return
            observed.add(signature)
            path = session.folder / ("target_" + item["kind"] + ".png")
            assert session.native("png %d %s" % (item["page"], path)) == "OK"
            with Image.open(item["expected"]) as expected, Image.open(path) as target:
                box = (item["x"], item["y"], item["x"] + expected.width, item["y"] + expected.height)
                actual = target.crop(box).convert("RGB")
                same = expected.convert("RGB").tobytes() == actual.tobytes()
                item.update(width=expected.width, height=expected.height, pixels_equal=same)
                actual.save(session.folder / ("copied_%s_%d.png" % (item["kind"], len(copied))))
            copied.append(item)
            assert same, item["kind"] + " thumbnail differs immediately after the source UI copy"

        def observe(name):
            original = providers[name]

            def call(owner, args, *rest):
                result = original(owner, args, *rest)
                if result == 0:
                    if name == "$copy_thumbnail":
                        slot = state["save_catalog"]["slot_%05d" % args[1]]
                        if slot and slot["thumbnail"]:
                            verify_copy({"kind": "saved", "slot": args[1],
                                           "page": args[2], "x": args[3], "y": args[4],
                                           "expected": str(session.local_path("save://" + slot["thumbnail"]))})
                    else:
                        thumb = state["current_thumbnail"]
                        if thumb:
                            path = session.folder / "expected_current.png"
                            assert session.native("png %d %s" % (thumb, path)) == "OK"
                            verify_copy({"kind": "current", "page": args[1],
                                           "x": args[2], "y": args[3], "expected": str(path)})
                return result
            providers[name] = call

        observe("$copy_thumbnail")
        observe(0x89ce8c53)
        reopened = open_story_save(session)
        row["reopened_path"] = reopened
        settle(session, 1000, 4000)
        if state["active_ui_script"] != "loadsave.mjo":
            return row | {"status": "native_dialog_fixture"if "native_dialog_fixture"in row["reopened_path"]else"different_source_menu",
                          "active_ui_script": state["active_ui_script"]}
        slot_area = next((a for a in state["ui_areas"].values() if a["args"][1] == 0), None)
        if slot_area is None:
            return row | {"status": "different_source_slot_layout"}
        a = slot_area["args"]
        tick(session, {"x": a[2] + a[4] / 2, "y": a[3] + a[5] / 2,
                       "buttons": 0, "held_buttons": 0}, 400)
        settle(session, 1000, 4000)
        row["frame"] = session.capture("save_menu_selected")
        # Preserve the copies even if a later source callback erases them.
        row["source_copy_checks"] = copied
        kinds = {item["kind"] for item in copied}
        contract = current_preview_contract(vm)
        row["current_preview_contract"] = contract
        # Original thumbnail-list layouts deliberately skip a separate
        # current preview. A source-required preview must still be present.
        assert "current" in kinds or contract["disabled"], "save menu did not draw the source-required current preview"
        assert "saved" in kinds, "hovered saved slot did not draw its saved preview"
        checks = []
        row["checks"] = checks
        # Some original UIs share one preview rectangle: hovering a saved
        # slot intentionally replaces the current preview. Check its last
        # owner, rather than requiring both images to occupy it at once.
        latest = {(item["page"], item["x"], item["y"], item["width"], item["height"]): item
                  for item in copied}
        for item in latest.values():
            if not state["pages"][item["page"]]:
                checks.append(item | {"final_target_released": True})
                continue
            path = session.folder / ("final_target_%d.png" % item["page"])
            assert session.native("png %d %s" % (item["page"], path)) == "OK"
            with Image.open(item["expected"]) as expected, Image.open(path) as target:
                box = (item["x"], item["y"], item["x"] + expected.width, item["y"] + expected.height)
                same = expected.convert("RGB").tobytes() == target.crop(box).convert("RGB").tobytes()
            checks.append(item | {"pixels_equal": same})
            assert same, item["kind"] + " thumbnail differs inside the final source UI target"
        row.update(status="passed", checks=checks, source_thumbnail_size=size,
                   source_copy_checks=copied, real_native_pixels=True,
                   player_saves_used=False)
    except Exception as error:
        row.update(status="failed", error=str(error))
    return row


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--game", action="append")
    parser.add_argument("--pages", type=int, default=12)
    parser.add_argument("--jobs", type=int, default=4)
    args = parser.parse_args()
    folder = archive_dir("save_menu_preview")
    report = {"status": "running", "timestamp": timestamp(), "cases": [],
              "device_accessed": False, "evidence": str(folder.relative_to(ROOT)),
              "requested_scene_pages": args.pages,
              "scope": "actual source save menu and hover, with current and saved thumbnail pixel comparisons"}
    with ProcessPoolExecutor(max_workers=args.jobs) as pool:
        jobs = {pool.submit(worker, g, str(folder), args.pages): g["id"]
                for g in read_json(ROOT / "config/recovered_library.json")["games"]
                if not args.game or g["id"] in args.game}
        for future in as_completed(jobs):
            try:
                row = future.result()
            except Exception as error:
                row = {"game": jobs[future], "status": "failed", "error": str(error)}
            report["cases"].append(row)
            print(json.dumps(row, ensure_ascii=True), flush=True)
            write_json(folder / "report.json", report)
            write_json(ROOT / "reports/save_menu_preview.json", report)
    report.update(status="complete", timestamp=timestamp())
    write_json(folder / "report.json", report)
    write_json(ROOT / "reports/save_menu_preview.json", report)
    return int(any(row["status"] == "failed" for row in report["cases"]))


if __name__ == "__main__":
    raise SystemExit(main())
