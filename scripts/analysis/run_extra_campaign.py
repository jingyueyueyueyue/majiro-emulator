"""Read earned original bonus/replay entries through their real title menus."""
import argparse
import os
from pathlib import Path

from model_session import Session
from lua_bridge import table_value
from probe_ending_extras import areas, settled_click
from port_common import ROOT, archive_dir, read_json, sha256, write_json


def run(checkpoint, name, bonus=(), replay=(), verify_only=False):
    assert name and all(c.isascii() and (c.isalnum() or c in "_-") for c in name)
    evidence = archive_dir("extra_" + name)
    saves = evidence / "saves"
    saves.mkdir()
    source = Path(checkpoint).resolve()
    before = {}
    for file in source.iterdir():
        if file.is_file() and not file.name.endswith(".writing") and file.name != "analysis-active.json":
            os.link(file, saves / file.name)
            before[file.name] = sha256(file)
    marker = saves / "analysis-active.json"
    write_json(marker, {"pid": os.getpid(), "purpose": "earned extra campaign"})
    hashes = {p.name: sha256(p) for p in (ROOT / "runtime").glob("*.lua")}
    s = Session(not verify_only, save_root=saves)
    s.vm["trace_limit"] = 0
    read = set()
    cases = []

    def pages(status, maximum=2500):
        count = 0
        choices = []
        for _ in range(maximum):
            if status == "text":
                for entry in s.system["state"]["console"]["page"].values():
                    read.add(entry["script"] + ":" + str(entry["offset"]))
                status = s.page()
                count += 1
            elif status == "ui":
                root = s.vm["frames"][1]["script"]["name"]
                if root == "start.mjo" and areas(s) in (list(range(1, 5)), list(range(1, 6)), list(range(1, 9))):
                    return status, count, choices
                if root.startswith("sub_title") and 99 in areas(s):
                    status = settled_click(s, 99)
                elif root in ("cgmode.mjo", "kaisou.mjo"):
                    return status, count, choices
                else:
                    choices.append({"root": root, "id": areas(s)[0]})
                    status = s.click(1)
            else:
                raise AssertionError((status, s.summary()))
        raise AssertionError(("extra campaign page budget", status, list(s.vm["scripts"].keys())))

    try:
        assert s.advance() == "ui"
        # Campaign checkpoints were captured before the ending's final flag
        # write. Finish the source ending normally and explicitly flush it.
        s.system["providers"]["$do_load"](s.vm, s.lua.table_from([9392]))
        status, ending_pages, _ = pages(s.advance(), 800)
        assert status == "ui" and areas(s) == list(range(1, 6)), s.summary()
        s.system["state"]["persist_dirty"] = True
        s.system["saves"]["flush"](s.vm)
        s = Session(not verify_only, save_root=saves)
        s.vm["trace_limit"] = 0
        assert s.advance() == "ui" and 5 in areas(s), s.summary()
        assert settled_click(s, 5) == "ui" and areas(s) == list(range(1, 9))
        for identifier in bonus:
            before_read = set(read)
            status, count, selections = pages(settled_click(s, identifier))
            assert status == "ui" and areas(s) == list(range(1, 9)), s.summary()
            item = {"bonus": identifier, "pages": count, "selections": selections,
                    "read_positions": sorted(read - before_read), "returned_to_extras": True}
            cases.append(item)
            print(name, "bonus", identifier, count, "pages returned", flush=True)
        if replay:
            assert settled_click(s, 2) == "ui", s.summary()
            print(name, "replay areas", areas(s), flush=True)
            for identifier in replay:
                before_read = set(read)
                status, count, selections = pages(settled_click(s, identifier))
                assert status == "ui", s.summary()
                assert 10000 in areas(s), s.summary()
                assert read != before_read, ("replay locked or did not run", identifier)
                cases.append({"replay": identifier, "pages": count, "selections": selections,
                              "read_positions": sorted(read - before_read), "returned_to_list": True})
                print(name, "replay", identifier, count, "pages returned", flush=True)
            assert settled_click(s, 10000) == "ui" and areas(s) == list(range(1, 9))
        s.system["saves"]["flush"](s.vm)
        assert all(sha256(source / file) == digest for file, digest in before.items())
        report = {"status": "passed", "cases": cases, "ending_pages": ending_pages,
                  "read_positions": sorted(read), "save_root": str(saves), "checkpoint": str(source),
                  "source_checkpoint_unchanged": True, "evidence": str(evidence),
                  "source_absent_audio": table_value(s.system["state"]["media"]["missing_requests"]),
                  "source_identity": read_json(ROOT / "resources/source_manifest.json")["source_identity"],
                  "runtime_hashes": hashes,
                  "runtime_files_changed_during_run": any(sha256(ROOT / "runtime" / n) != h for n, h in hashes.items()),
                  "scope": "earned ending; cold title; original bonus/replay clicks and natural text; no flag/PC edits"}
        write_json(ROOT / ("reports/extra_campaign_" + name + ".json"), report)
        write_json(evidence / "20261109_result.json", report)
        return report
    except Exception:
        write_json(evidence / "20261109_failure.json", s.summary())
        raise
    finally:
        marker.unlink(missing_ok=True)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--checkpoint", type=Path, required=True)
    parser.add_argument("--name", required=True)
    parser.add_argument("--bonus", type=int, nargs="*", default=[])
    parser.add_argument("--replay", type=int, nargs="*", default=[])
    parser.add_argument("--verify-only", action="store_true")
    args = parser.parse_args()
    run(args.checkpoint, args.name, args.bonus, args.replay, args.verify_only)
