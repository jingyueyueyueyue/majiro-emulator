"""Finish an earned campaign checkpoint, then inspect the original unlocked menus."""
import argparse
import os
from pathlib import Path
from model_session import Session
from test_settings import click_id
from test_title_render import render_state
from port_common import ROOT, archive_dir, write_json, sha256


def settled_click(s, identifier):
    result = click_id(s, identifier)
    if result == "ui":
        s.system["state"]["ui_waiting"] = False
        result = s.advance()
    assert result != "blocked", s.summary()
    return result


def areas(s):
    return [a["args"][1] for a in s.system["state"]["ui_areas"].values()]


def run(checkpoint, verify_only=False):
    evidence = archive_dir("ending_extras")
    saves = evidence / "saves"
    saves.mkdir()
    original_index = {}
    for file in Path(checkpoint).resolve().iterdir():
        if file.is_file() and not file.name.endswith(".writing"):
            os.link(file, saves / file.name)
            original_index[file.name] = sha256(file)
    s = Session(not verify_only, save_root=saves)
    assert s.advance() == "ui"
    s.system["providers"]["$do_load"](s.vm, s.lua.table_from([9392]))
    status = s.advance()
    count = 0
    while status == "text" and count < 400:
        status = s.page(); count += 1
    assert status == "ui" and 99 in areas(s), s.summary()
    assert settled_click(s, 99) == "ui", s.summary()
    assert areas(s) == [1, 2, 3, 4, 5], s.summary()
    s.system["state"]["persist_dirty"] = True
    s.system["saves"]["flush"](s.vm)
    # New VM and cold library initialization, without editing unlock flags.
    s = Session(not verify_only, save_root=saves)
    assert s.advance() == "ui" and areas(s) == [1, 2, 3, 4, 5], s.summary()
    assert settled_click(s, 5) == "ui", s.summary()
    assert areas(s) == list(range(1, 9)), s.summary()
    cases = []
    for identifier, name in ((3, "music"), (1, "cg"), (2, "replay"), (4, "staffroom")):
        status = settled_click(s, identifier)
        report = {"status": status, "areas": areas(s),
                  "frames": [f["script"]["name"] for f in s.vm["frames"].values()]}
        print(name, report, flush=True)
        screenshot = evidence / ("20261109_" + name + ".png")
        render_state(s, screenshot)
        cases.append({"menu": name, **report, "screenshot": str(screenshot)})
        if name == "cg":
            for control in (10002, 10001):
                assert settled_click(s, control) == "ui", s.summary()
            viewer = settled_click(s, 0)
            s.clock["ms"] += 500
            s.system["state"]["ui_waiting"] = False
            s.event(x=640, y=360, buttons=2)
            assert s.advance() == "ui", s.summary()
            cases[-1]["viewer_status"] = viewer
            cases[-1]["page_controls"] = [10002, 10001]
            assert settled_click(s, 10000) == "ui", s.summary()
        elif status == "ui" and 999 in areas(s):
            assert settled_click(s, 999) == "ui", s.summary()
        elif status == "ui" and name == "replay" and 10000 in areas(s):
            assert settled_click(s, 10000) == "ui", s.summary()
        elif status == "ui" and name == "replay" and 99 in areas(s):
            assert settled_click(s, 99) == "ui", s.summary()
        elif name == "staffroom" and status == "text":
            pages = 0
            while status == "text" and pages < 50:
                status = s.page(); pages += 1
            s.system["state"]["ui_waiting"] = False
            status = s.advance()
            assert status == "ui", s.summary()
            print("staff menu", areas(s), flush=True)
            cases[-1]["introduction_pages"] = pages
            comments = []
            staff_cfg = s.lua.execute((ROOT / "resources/scripts/config_files.lua").read_text(encoding="utf-8"))["staffroom.cfg"]
            for author in (0, 1, 2, 3):
                status = settled_click(s, author)
                pages = 0
                while status == "text" and pages < 500:
                    status = s.page(); pages += 1
                s.system["state"]["ui_waiting"] = False
                status = s.advance()
                assert status == "ui", s.summary()
                expected_script = staff_cfg[author * 2 + 2] + ".mjo"
                assert s.vm["scripts"][expected_script], ("staff comment was not entered", expected_script)
                print("staff comment", author, pages, areas(s), flush=True)
                comments.append({"author": author, "pages": pages, "source_script": expected_script,
                                 "timed_image_only": author == 1})
            cases[-1]["comments"] = comments
            s.clock["ms"] += 500
            s.system["state"]["ui_waiting"] = False
            s.event(x=640, y=360, buttons=2)
            assert s.advance() == "ui", s.summary()
        else:
            break
        assert areas(s) == list(range(1, 9)), (name, s.summary())
    assert all(sha256(Path(checkpoint) / name) == digest for name, digest in original_index.items())
    result = {"status": "passed", "chapter_pages": count, "root_return_to_title": True,
              "cold_restart_unlock_retained": True, "source_checkpoint_unchanged": True,
              "checkpoint": str(checkpoint), "cases": cases, "evidence": str(evidence),
              "all_four_menus_opened": len(cases) == 4,
              "scope": "earned final chapter checkpoint through normal clicks; no unlock injection"}
    write_json(ROOT / "reports/ending_extras_tests.json", result)
    write_json(evidence / "20261109_result.json", result)
    return result


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("--checkpoint", type=Path, required=True)
    p.add_argument("--verify-only", action="store_true")
    args = p.parse_args()
    run(args.checkpoint, args.verify_only)
