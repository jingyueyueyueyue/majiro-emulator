"""Enter actual title buttons and report the next runtime dependency."""
from __future__ import annotations
import argparse
import json
from pathlib import Path
import sys

from model_session import Session
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/"convert"))
from port_common import ROOT, write_json


def run(choice=1,verify_only=False):
    session=Session(allow_prepare=not verify_only)
    status=session.advance()
    if status!="ui":
        raise RuntimeError("original title not reached: "+str(session.summary()))
    title_steps=session.vm["steps"]
    try:
        status=session.click(choice)
    except RuntimeError as error:
        from lua_bridge import table_value
        report=session.summary()
        report["threads"]={str(id):{"finished":thread["finished"],
            "waiting":table_value(thread["waiting"])if thread["waiting"]else None,
            "frames":[{"script":frame["script"]["name"],
                       "offset":frame["script"]["code"][frame["index"]][1]}
                      for frame in thread["frames"].values()]}
            for id,thread in session.vm["threads"].items()}
        write_json(ROOT/"reports"/("title_path_%d_failure.json"%choice),report)
        raise
    report=session.summary()
    report.update({"scope":"normal title pointer input","choice":choice,"title_steps":title_steps,
                   "navigation_status":status,"release_allowed":False})
    write_json(ROOT/"reports"/("title_path_%d.json"%choice),report)
    display={k:report[k]for k in ("choice","steps","navigation_status","problem")}
    display["last_syscall"]={k:v for k,v in (report.get("last_syscall")or {}).items() if k!="args"}
    print(json.dumps(display,
                     ensure_ascii=True))
    return report


if __name__=="__main__":
    parser=argparse.ArgumentParser()
    parser.add_argument("--choice",type=int,default=1)
    parser.add_argument("--verify-only",action="store_true")
    args=parser.parse_args()
    run(args.choice,args.verify_only)
