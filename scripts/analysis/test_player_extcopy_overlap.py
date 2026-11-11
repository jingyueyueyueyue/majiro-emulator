"""Original ordered overlapping extcopy versus the shared portable algorithm."""
import json
from pathlib import Path
import random
import sys

from original_graphics_remaining import GraphicsOracle
from test_screen_semantics import Portable
from audit_player_semantics import registered_providers

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/"convert"))
from port_common import ROOT,archive_dir,sha256,timestamp,write_json


def run():
    folder=archive_dir("extcopy_overlap")
    portable=Portable(folder)
    samples=json.loads((ROOT/"reports/opcode_dispatch.json").read_text(encoding="utf8"))["samples"]
    entries=json.loads((ROOT/"reports/syscall_dispatch.json").read_text(encoding="utf8"))["samples"]
    report={"status":"passed","timestamp":timestamp(),"cases":0,"failures":[],"samples":[]}
    rng=random.Random(20261112)
    try:
        for sample in samples:
            declarations=next(row["entries"]for row in entries if row["exe_sha256"]==sample["exe_sha256"])
            original=GraphicsOracle(sample,declarations);cases=0;failures=0
            profile=original.extcopy_profile()
            lua,_,_,system=registered_providers(overrides={
                "graphics_controls":profile,"graphics":lambda request:portable(request)[0]})
            vm=lua.table()
            system["state"]["pages"][1]=lua.table_from({"width":6,"height":4,"depth":32})
            for depth,masked in((32,False),(32,True),(8,False)):
                rectangles=((0,0,5,1,1,0),(1,0,5,1,0,0),(0,0,5,3,0,1),(0,1,5,3,0,0),
                            (0,0,3,1,1,0),(1,0,3,1,0,0),(0,0,2,1,1,0))
                for operation,rect in [(op,rect)for op in("extcopy","copy")for rect in rectangles]:
                    for anti in (0,1,64,127,254,255)if operation=="extcopy"else(0,):
                        width,height=6,4
                        original.cursor=original.DATA+0x30000
                        original.registered_pages=[];original.write_u32(original.page_head,0)
                        raw=bytes(rng.randrange(256)if depth==8 or i%4!=3 else 255
                                  for i in range(width*height*(depth//8)))
                        mask=bytes(rng.randrange(256)for _ in range(width*height))if masked else None
                        page=original.page(width,height,raw,depth,mask)
                        portable("reset");portable(f"create 1 {width} {height} 0 {depth}")
                        if masked:portable("alpha 1 2 0")
                        for at in range(width*height):
                            if depth==8:color=raw[at]
                            else:
                                b,g,r,_=raw[at*4:at*4+4];color=r+g*256+b*65536
                            portable(f"set 1 {at%width} {at//width} {color}")
                            if masked:portable(f"set 2 {at%width} {at//width} {mask[at]}")
                        sx,sy,w,h,dx,dy=rect
                        arguments=[page["page"],sx,sy,w,h,page["page"],dx,dy]
                        if operation=="extcopy":arguments.append(anti)
                        original.call_graphics("$grp_"+operation,arguments)
                        actual_args=[1,sx,sy,w,h,1,dx,dy]
                        if operation=="extcopy":actual_args.append(anti)
                        assert system["providers"]["$grp_"+operation](vm,lua.table_from(actual_args))==0
                        expected=bytes(original.machine.mem_read(page["pixels"],page["size"]))
                        expected_mask=bytes(original.machine.mem_read(page["mask"]["pixels"],width*height))if masked else None
                        mismatch=None
                        for at in range(width*height):
                            rgba=tuple(map(int,portable(f"pixel 1 {at%width} {at//width}")[0].split()[1:]))
                            if depth==8:want=(expected[at],)*3
                            else:
                                bpp=page["depth"]//8;want=tuple(expected[at*bpp:at*bpp+3][::-1])
                            if rgba[:3]!=want or(expected_mask is not None and rgba[3]!=255-expected_mask[at]):
                                mismatch={"pixel":at,"actual":rgba,"expected_rgb":want,
                                          "expected_alpha":255-expected_mask[at]if masked else 255};break
                        cases+=1
                        if mismatch:
                            failures+=1
                            if len(report["failures"])<300:
                                report["failures"].append({"sample":sample["sample"],"operation":operation,"depth":depth,"masked":masked,
                                                          "rectangle":rect,"anti":anti,**mismatch})
            report["samples"].append({"sample":sample["sample"],"cases":cases,"failures":failures,
                                      "profile":profile})
            report["cases"]+=cases
            print(sample["sample"],cases,"cases",failures,"mismatches",flush=True)
    finally:
        portable.close()
    report["mismatches"]=sum(row["failures"]for row in report["samples"])
    if report["mismatches"]:report["status"]="failed"
    report["native_source_sha256"]=sha256(ROOT/"player/native/majiro_pixels.cpp")
    report["syscall_source_sha256"]=sha256(ROOT/"player/runtime/majiro_syscalls.lua")
    report["independent_syscall_and_portable_pipeline"]=True
    write_json(folder/"report.json",report);write_json(ROOT/"reports/player_extcopy_overlap_tests.json",report)
    return report


if __name__=="__main__":
    raise SystemExit(run()["status"]!="passed")
