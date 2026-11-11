"""Measure lossless native composition/export of actual title pages."""
from __future__ import annotations
import argparse
import ctypes
import statistics
from pathlib import Path
import sys
import time
from model_session import Session
from native_pixels import Pixels
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/"convert"))
from port_common import ROOT,archive_dir,sha256,write_json,native_pixels_library


def run(label="baseline",operation="render"):
    session=Session(allow_prepare=False)
    assert session.advance()=="ui",session.summary()
    state=session.system["state"];rows=[]
    for id in state["sprite_order"].values():
        s=state["sprites"][id]
        if s and s["destination"]==state["frontbuffer"] and s["anti"]<255 and(s["hidden"]or 0)==0:
            values=[s[k]for k in("page","sx","sy","width","height","x","y","xscale","yscale","rotate","anti")]
            rows.append(" ".join(map(str,values))+(" 1"if s["extended"]else" 0"))
    api=Pixels.__new__(Pixels)
    api.dll=ctypes.CDLL(str(native_pixels_library()))
    api.dll.RuriPixels.argtypes=[ctypes.c_char_p];api.dll.RuriPixels.restype=ctypes.c_void_p
    api.dll.Delete.argtypes=[ctypes.c_void_p];api.dll.Delete.restype=None
    evidence=archive_dir("presenter_benchmark");path=evidence/(evidence.name[:8]+"_frame.png")
    command="%s %d %s\n%s"%(operation,state["frontbuffer"],path,"\n".join(rows))
    timings=[]
    for _ in range(25):
        began=time.perf_counter();reply=api(command)
        if not reply.startswith("OK"):raise RuntimeError(reply)
        timings.append((time.perf_counter()-began)*1000)
    report={"status":"passed","label":label,"operation":operation,"frames":len(timings),
        "median_ms":statistics.median(timings),"p95_ms":sorted(timings)[int(len(timings)*.95)],
        "first_ms":timings[0],"file_bytes":path.stat().st_size,"screenshot":str(path),
        "dll_sha256":sha256(native_pixels_library()),
        "scope":"actual source title pages; CPU composition and file export only"}
    write_json(ROOT/"reports"/("presenter_"+label+".json"),report)
    print(report)
    return report


if __name__=="__main__":
    parser=argparse.ArgumentParser();parser.add_argument("--label",default="baseline")
    parser.add_argument("--operation",default="render",choices=("render","renderfast","present"))
    args=parser.parse_args();run(args.label,args.operation)
