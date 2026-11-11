"""Extend only unresolved history/save menu cases to later legal story scenes."""
from concurrent.futures import ProcessPoolExecutor, as_completed
from pathlib import Path
import argparse
import json
import os
import sys
import time

sys.path[:0] = [str(Path(__file__).resolve().parents[1] / "convert"),
               str(Path(__file__).resolve().parents[1] / "build")]
from port_common import ROOT, archive_dir, read_json, timestamp, write_json
from run_background_compatibility import source_identity


def worker(game, targets, folder, pages):
    import test_history_voice_batch as history
    import test_save_menu_preview as saves
    from test_story_entry_regressions import run_story

    def later_story(game, files, mode, requested_pages, seconds, session=None):
        return run_story(game, files, mode, pages, max(seconds, 180), session=session)
    # Only the fixture's requested scene depth changes. Original title/story
    # inputs, VM execution, save storage, graphics and assertions stay intact.
    history.run_story = later_story
    saves.run_story = later_story
    began = time.monotonic()
    row = {"game": game["id"], "requested_scene_pages": pages, "results": []}
    for target in targets:
        try:
            value = (history.worker(game, str(Path(folder) / "history"))
                     if target == "history" else
                     [saves.worker(game, str(Path(folder) / "save"))])
            for item in value:
                row["results"].append(item | {"target": target, "requested_scene_pages": pages})
        except Exception as error:
            row["results"].append({"target": target, "status": "failed", "error": str(error)})
    row["seconds"] = round(time.monotonic() - began, 2)
    return row


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--pages", type=int, default=300)
    parser.add_argument("--jobs", type=int, default=3)
    parser.add_argument("--game", action="append")
    args = parser.parse_args()
    pending = {}
    inputs = {}
    for target, name in (("history", "history_voice_batch"), ("save", "save_menu_preview")):
        path = ROOT / ("reports/" + name + ".json")
        report = read_json(path)
        inputs[target] = {"report": str(path.relative_to(ROOT)), "evidence": report["evidence"]}
        for row in report["cases"]:
            if row["status"] not in ("passed", "failed", "worker_failed"):
                pending.setdefault(row["game"], set()).add(target)
    games = [g for g in read_json(ROOT / "config/recovered_library.json")["games"]
             if g["id"] in pending and (not args.game or g["id"] in args.game)]
    folder, identity = archive_dir("deeper_menu_paths"), source_identity()
    report = {"status": "running", "timestamp": timestamp(), "pid": os.getpid(),
              "source_identity": identity, "requested_scene_pages": args.pages,
              "scope": "only unresolved source menu paths, legal title-to-story traversal and existing actual-pixel assertions",
              "device_accessed": False, "player_saves_used": False,
              "baseline_reports": inputs, "cases": [],
              "games_requested": [g["id"] for g in games],
              "evidence": str(folder.relative_to(ROOT))}

    def publish():
        report["updated"] = timestamp()
        write_json(folder / "report.json", report)
        write_json(ROOT / "reports/deeper_menu_paths.json", report)
    publish()
    with ProcessPoolExecutor(max_workers=args.jobs) as pool:
        jobs = {pool.submit(worker, g, sorted(pending[g["id"]]), str(folder), args.pages): g["id"]
                for g in games}
        for future in as_completed(jobs):
            try:
                row = future.result()
            except Exception as error:
                row = {"game": jobs[future], "results": [{"status": "failed", "error": str(error)}]}
            report["cases"].append(row)
            publish()
            print(json.dumps({"game": row["game"], "seconds": row.get("seconds"),
                              "results": [{k: v.get(k) for k in ("target", "mode", "status", "error")}
                                          for v in row["results"]]}, ensure_ascii=True), flush=True)
    results = [r for g in report["cases"] for r in g["results"]]
    counts = {name: sum(r["status"] == name for r in results)
              for name in sorted({r["status"] for r in results})}
    report.update(status="complete", summary=counts,
                  source_changed_during_run=source_identity() != identity)
    publish()
    return int(bool(counts.get("failed")) or report["source_changed_during_run"])


if __name__ == "__main__":
    raise SystemExit(main())
