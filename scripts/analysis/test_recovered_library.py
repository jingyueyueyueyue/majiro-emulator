"""Run source startup and story checks directly against the recovered tablet library.

This uses the existing VM/real resource decoder, isolated in-memory saves, and a
local delayed translation fixture. It never connects to a tablet or a real API.
"""
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor, as_completed
from collections import Counter
import argparse
import json
import sys
import time

sys.path[:0] = [str(Path(__file__).resolve().parents[1] / "convert"),
               str(Path(__file__).resolve().parents[1] / "build")]
from port_common import ROOT, archive_dir, write_json, read_json, timestamp, sha256
from audit_startup_resources import Startup
from test_story_entry_regressions import run_story
from test_chapter_regressions import game_chapters


def library():
    config = read_json(ROOT / "config/recovered_library.json")
    rows = []
    for game in config["games"]:
        directory = Path(game["directory"])
        files = {p.relative_to(directory).as_posix(): p for p in directory.rglob("*")
                 if p.is_file() and not any(part.lower() in ("savedata", "save", "saves", "save_chs")
                                           for part in p.relative_to(directory).parts)}
        rows.append((game, files))
    return rows


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--game", action="append")
    parser.add_argument("--mode", action="append", choices=("original", "local", "online"))
    parser.add_argument("--stage", choices=("startup", "story", "chapters"), default="startup")
    parser.add_argument("--repeats", type=int, default=1)
    parser.add_argument("--pages", type=int, default=10)
    parser.add_argument("--seconds", type=int, default=30)
    parser.add_argument("--workers", type=int, default=4)
    args = parser.parse_args()
    folder = archive_dir("recovered_library_" + args.stage)
    source_hashes = {str(p.relative_to(ROOT)): sha256(p) for p in (ROOT / "player/runtime").glob("*.lua")}
    report = {"timestamp": timestamp(), "status": "running", "stage": args.stage, "cases": [],
              "device_accessed": False, "player_saves_used": False, "real_translation_api_used": False,
              "visual_render_verified": False, "full_routes_verified": False,
              "source_sha256": source_hashes, "evidence": str(folder.relative_to(ROOT))}
    jobs = [(game, files, mode, repeat) for game, files in library()
            if not args.game or game["id"] in args.game
            for mode in (args.mode or ("original", "online"))
            for repeat in range(args.repeats)]
    def run(job):
        game, files, mode, repeat = job
        try:
            if args.stage == "startup":
                row = Startup(game, files, mode, 2000000, args.seconds).run()
                row["status"] = "failed" if row["error"] else (
                    "passed" if row["stop"] in ("source_ui_wait", "first_text_wait", "input_boundary")
                    else "boundary")
            elif args.stage == "story":
                row = run_story(game, files, mode, args.pages, args.seconds)
            else:
                row = game_chapters(game, files, mode, args.pages, args.seconds,
                    lambda progress: write_json(folder / (game["id"] + "_" + mode + "_chapters.json"), progress))
            row["repeat"] = repeat
        except Exception as error:
            row = {"game": game["id"], "mode": mode, "repeat": repeat, "status": "failed", "error": str(error)}
        return row
    with ThreadPoolExecutor(max_workers=args.workers) as pool:
        for future in as_completed([pool.submit(run, job) for job in jobs]):
            row = future.result()
            report["cases"].append(row)
            write_json(folder / "report.json", report)
            print(json.dumps({k: row.get(k) for k in ("game", "mode", "repeat", "status", "pages", "stop", "error")},
                             ensure_ascii=True), flush=True)
    report["status"] = "complete"
    report["summary"] = dict(Counter(row["status"] for row in report["cases"]))
    report["source_changed_during_run"] = source_hashes != {
        str(p.relative_to(ROOT)): sha256(p) for p in (ROOT / "player/runtime").glob("*.lua")}
    write_json(folder / "report.json", report)
    write_json(ROOT / "reports" / ("recovered_library_" + args.stage + ".json"), report)
    return int(report["summary"].get("failed", 0) > 0 or report["source_changed_during_run"])


if __name__ == "__main__":
    raise SystemExit(main())
