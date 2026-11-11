"""Continue natural route choices with compact coverage and reusable source checkpoints."""
import argparse
import json
import os
import time
from pathlib import Path
from model_session import Session
from lua_bridge import table_value
from port_common import ROOT, archive_dir, read_json, write_json, sha256


SCORES = {"bright": 710383187, "death": 223112338, "yura": 3694920119, "iichiko": 828147412}


def route_option(s, route):
    """Read the source switch's score increments, then click that actual option."""
    frame = s.vm["frames"][1]
    if not route or not frame["script"]["name"].startswith("00"):
        return 1
    code = frame["script"]["code"]
    options = {}
    positions = frame["script"]["positions"]
    mnemonic = lambda row: s.vm["opcodes"][row[2]][1]
    start = next((positions[code[i][3]] for i in range(int(frame["index"]),
        min(len(code), int(frame["index"]) + 15) + 1) if mnemonic(code[i]) == "br.case"), None)
    for _ in range(8):
        if start is None:
            break
        branch = next((i for i in range(int(start), min(len(code), int(start) + 5) + 1)
                       if mnemonic(code[i]) == "bne.case" and mnemonic(code[i-1]) == "ldc.i"), None)
        if branch is None:
            break
        case = code[branch-1][3]
        following = positions[code[branch][3]]
        for i in range(branch+1, int(following)):
            row = code[i]
            if mnemonic(row) == "st.add.i" and row[4] in SCORES.values():
                options[row[4]] = case
        start = following
    wanted = options.get(SCORES[route])
    if wanted is not None:
        for i, area in s.system["state"]["ui_areas"].items():
            args = area["args"]
            # The source menu helper returns area id + 1 to the story.
            if args[1] == wanted - 1 and args[2] == 0 and args[4] == 1280:
                return i
    return 1


def run(name, pages=6000, choices=None, resume=None, slot=9392, verify_only=False, route=None, seed=None):
    assert name and all(c.isascii() and (c.isalnum() or c in "_-") for c in name)
    runtime_hashes = {p.name: sha256(p) for p in (ROOT / "runtime").glob("*.lua")}
    evidence = archive_dir("route_" + name)
    active = evidence / "analysis-active.json"
    write_json(active, {"pid": os.getpid(), "purpose": "route campaign save updates"})
    saves = Path(resume).resolve() if resume else evidence / "saves"
    if seed:
        assert not resume, "choose resume or isolated hardlink seed"
        saves.mkdir()
        for file in Path(seed).resolve().iterdir():
            if file.is_file() and not file.name.endswith(".writing") and file.name != "analysis-active.json":
                os.link(file, saves / file.name)
    saves.mkdir(parents=True, exist_ok=True)
    save_active = saves / "analysis-active.json"
    write_json(save_active, {"pid": os.getpid(), "purpose": "route campaign save updates"})
    s = Session(not verify_only, save_root=saves)
    # Keep the full source/error frames; a per-instruction rolling trace is
    # unnecessary for a long coverage run and costs time without more coverage.
    s.vm["trace_limit"] = 0
    assert s.advance() == "ui", s.summary()
    if resume or seed:
        assert s.system["state"]["save_catalog"]["slot_%05d" % slot], "source checkpoint missing"
        assert s.system["providers"]["$do_load"](s.vm, s.lua.table_from([slot])) != -1
        # Player checkpoints may have SKIP/AUTO enabled. Turn those modes
        # off through the same provider as normal controls so each page is
        # counted and one advance does not run thousands of unseen lines.
        s.system["providers"]["$reset_fast_mode"](s.vm, s.lua.table())
        s.event(buttons=0, held_buttons=0, down={}, pressed={})
        status = s.advance()
    else:
        status = s.click(1)
    selected = []
    read = set()
    chapters = {}
    terminal = None
    begun = time.monotonic()
    choices = choices or []
    choice_number = 0
    checkpoint_count = 0
    def finish(failure=None):
        report = {
            "status": "failed" if failure or s.vm["problem"] else "passed",
            "terminal": terminal, "navigation_status": status, "failure": failure,
            "problem": table_value(s.vm["problem"]), "pages": len(read),
            "last_syscall": table_value(s.vm["last_syscall"]),
            "failure_frames": [{"script": f["script"]["name"],
                                "offset": f["script"]["code"][f["index"]][1]}
                               for f in s.vm["frames"].values()] if failure else None,
            "read_positions": sorted(read), "selections": selected,
            "loaded_scripts": sorted(s.vm["scripts"].keys()), "steps": s.vm["steps"],
            "save_root": str(saves), "evidence": str(evidence), "route_policy": route,
            "seconds": round(time.monotonic() - begun, 2),
            "source_identity": read_json(ROOT / "resources/source_manifest.json")["source_identity"],
            "runtime_hashes": runtime_hashes,
            "runtime_files_changed_during_run": any(sha256(ROOT / "runtime" / key) != value
                                                    for key, value in runtime_hashes.items()),
            "source_absent_audio": table_value(s.system["state"]["media"]["missing_requests"]),
            "missing_picture_queries": table_value(s.system["state"]["missing_picture_queries"]),
            "all_routes_complete": False,
            "scope": "normal original title/story/chapter choices; source save points, no flag or PC edits"}
        write_json(ROOT / ("reports/route_campaign_" + name + ".json"), report)
        write_json(evidence / "20261109_route.json", report)
        active.unlink(missing_ok=True)
        save_active.unlink(missing_ok=True)
        return report
    try:
        for page in range(pages):
            if page % 100 == 0:
                print(name, "progress", page, "pages", s.vm["steps"], "steps", flush=True)
                if status == "text":
                    s.system["providers"]["$do_save"](s.vm, s.lua.table_from([9392]))
            if status == "text":
                for item in s.system["state"]["console"]["page"].values():
                    read.add(item["script"] + ":" + str(item["offset"]))
                status = s.page()
            elif status == "ui":
                frames = list(s.vm["frames"].values())
                areas = s.system["state"]["ui_areas"]
                root = frames[0]["script"]["name"] if frames else ""
                if root == "start.mjo":
                    terminal = "returned_to_title"
                    break
                chapter = root.startswith("sub_title")
                if chapter:
                    candidates = [(a["args"][1], i) for i, a in areas.items() if 0 < a["args"][1] < 99]
                    latest, index = max(candidates) if candidates else (0, None)
                    if latest <= chapters.get(root, 0):
                        index = next(i for i, a in areas.items() if a["args"][1] == 99)
                    else:
                        chapters[root] = latest
                else:
                    index = choices[choice_number] if choice_number < len(choices) else route_option(s, route)
                    choice_number += 1
                    # Preserve the game's most recent source checkpoint. It
                    # may be before the choice; restore by ordinary page input.
                    if s.system["saves"]["stats"]()["checkpoint"]:
                        checkpoint_count += 1
                        s.system["providers"]["$do_save"](s.vm, s.lua.table_from([9400 + checkpoint_count]))
                selected.append({
                    "root": root, "chapter": chapter, "index": index,
                    "id": areas[index]["args"][1],
                    "source_slot": None if chapter else 9400 + checkpoint_count,
                    "areas": [list(a["args"].values())[:5] for a in areas.values()],
                    "frames": [{"script": f["script"]["name"], "offset": f["script"]["code"][f["index"]][1]}
                               for f in frames]})
                print(name, "choice", root, index, areas[index]["args"][1], flush=True)
                status = s.click(index)
                selected[-1]["scores_after"] = {name: s.vm["globals"][1][key] or 0
                                                for name, key in SCORES.items()}
            else:
                terminal = "unexpected_" + status
                break
        if status == "text":
            s.system["providers"]["$do_save"](s.vm, s.lua.table_from([9392]))
        result = finish()
        assert result["status"] == "passed", result["problem"]
        print(name, "ended", terminal, status, "read", len(read), flush=True)
        return result
    except Exception as error:
        finish(str(error))
        raise


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("--name", required=True)
    p.add_argument("--pages", type=int, default=6000)
    p.add_argument("--choices", default="")
    p.add_argument("--resume", type=Path)
    p.add_argument("--slot", type=int, default=9392)
    p.add_argument("--verify-only", action="store_true")
    p.add_argument("--route", choices=tuple(SCORES))
    p.add_argument("--seed", type=Path)
    args = p.parse_args()
    run(args.name, args.pages, [int(v) for v in args.choices.split(",") if v],
        args.resume, args.slot, args.verify_only, args.route, args.seed)
