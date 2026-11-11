"""Vary host tick duration/input timing and enforce a first visible scene."""
from pathlib import Path
from concurrent.futures import ProcessPoolExecutor,as_completed
import argparse
import hashlib
import json
import random
import sys
import time
sys.path[:0]=[str(Path(__file__).resolve().parents[1]/"convert")]
from port_common import ROOT,archive_dir,read_json,write_json,timestamp
from audit_startup_resources import Startup
from lua_bridge import table_value
from desktop_pixel_session import PixelSession


def worker(game,folder,repeats):
    d=Path(game["directory"]);files={p.relative_to(d).as_posix():p for p in d.rglob("*")if p.is_file()}
    rows=[]
    for number in range(repeats):
        mode=("original","online")[number%2]
        session=PixelSession(game,files,mode,Path(folder)/game["id"].replace(" ","_")/str(number))
        # Reuse the actual bootstrap but stop before its first run-loop tick,
        # then drive production PLAYER_TICK with varied frame cadences.
        session.maximum=0;boot=session.run()
        rng=random.Random(number+171027)
        row={"game":game["id"],"repeat":number,"mode":mode,"status":"running"}
        began=time.monotonic();settled=0
        try:
            for tick in range(5000):
                delta=(1,8,16,33,100,250)[number%6]if number<6 else rng.choice([1,8,16,33,100,250])
                session.clock+=delta
                session.lua.globals()["PLAYER_TICK"](session.clock,session.lua.table_from({
                    "x":511.75,"y":287.625,"buttons":0,"held_buttons":0}))
                state=session.system["state"]
                if state["ui_waiting"]or state["reading_wait"]:
                    settled+=delta
                    if settled>=1500:break
                else:settled=0
                if time.monotonic()-began>60:break
            stats=session.lua.globals()["PLAYER_STATUS"]()
            row.update(status="passed"if session.rendered_frames else"failed",
                       native_frames=session.rendered_frames,invalidate_depth=state["invalidate_depth"],
                       submitted_frames=stats["submitted_frames"],steps=session.vm["steps"],
                       ui_waiting=bool(state["ui_waiting"]),reading_wait=bool(state["reading_wait"]))
            row["frame"]=session.capture("settled")
            if not row["frame"].get("nonblack"):row.update(status="failed",error="no nonblack frame at source input wait")
        except Exception as error:row.update(status="failed",error=str(error))
        rows.append(row)
    return rows


def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument("--repeats",type=int,default=6)
    p.add_argument("--jobs",type=int,default=4);args=p.parse_args();folder=archive_dir("startup_timing_batch")
    report={"status":"running","timestamp":timestamp(),"cases":[],"device_accessed":False,
            "evidence":str(folder.relative_to(ROOT)),"real_native_pixels":True}
    with ProcessPoolExecutor(max_workers=args.jobs)as pool:
        for f in as_completed([pool.submit(worker,g,str(folder),args.repeats)
                              for g in read_json(ROOT/"config/recovered_library.json")["games"]]):
            try:rows=f.result()
            except Exception as e:rows=[{"status":"worker_failed","error":str(e)}]
            report["cases"].extend(rows)
            for row in rows:print(json.dumps({k:row.get(k)for k in("game","repeat","mode","status","invalidate_depth","error")},ensure_ascii=True),flush=True)
            write_json(folder/"report.json",report);write_json(ROOT/"reports/startup_timing_batch.json",report)
    report.update(status="complete",timestamp=timestamp())
    write_json(folder/"report.json",report);write_json(ROOT/"reports/startup_timing_batch.json",report)
    return int(any(row["status"]!="passed"for row in report["cases"]))


if __name__=="__main__":raise SystemExit(main())
