"""Original syscall and pixel differential cases for remaining page operations."""
from __future__ import annotations

import json
from pathlib import Path
import random
import sys

from audit_player_semantics import registered_providers, source_identity
from native_pixels import Pixels
from original_graphics_remaining import GraphicsOracle
from test_screen_semantics import Portable

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "build"))
from build_player_native import build
from port_common import ROOT, archive_dir, player_pixels_library as native_pixels_library, sha256, timestamp, write_json


def run():
    build()
    before=source_identity()
    folder=archive_dir("graphics_remaining_tests")
    samples=json.loads((ROOT/"reports/opcode_dispatch.json").read_text(encoding="utf-8"))["samples"]
    dispatch={row["sample"]:row["entries"]for row in
              json.loads((ROOT/"reports/syscall_dispatch.json").read_text(encoding="utf-8"))["samples"]}
    report={"schema":1,"timestamp":timestamp(),"source_identity":before,"samples":[],
            "failures":[],"scope":"real syscall argument/result paths and pixel loops; original page "
                "registry populated, CRT random algorithm retained with fixture thread-local seed",
            "limits":["GDI invalidation scheduling is outside these memory fixtures",
                      "fuchidori_copy and raw alpha storage-byte effects remain separate targets"],
            "native_library_sha256":sha256(native_pixels_library()),"complete_engine_semantics":False}
    desktop=Pixels();portable=Portable(folder);rng=random.Random(171027)
    report["portable_generated_sha256"]=portable.generated_sha256
    def pixels(request):
        actual=desktop(request)
        expected=portable(request)[0]
        assert actual==expected,("desktop/Android provider mismatch",request,actual,expected)
        return actual
    rectangles=[(0,0,8,5,0,0),(-2,0,7,5,1,0),(0,-1,8,5,0,1),
                (2,1,7,5,-1,-2),(0,0,0,4,1,1),(0,0,-1,3,0,0),
                (0,0,7,5,1,0),(0,0,8,4,0,1),(1,1,6,3,0,0),(20,20,3,3,0,0)]
    for sample in samples:
        oracle=GraphicsOracle(sample,dispatch[sample["sample"]])
        profile={"optional_mulcopy_coordinates":oracle.optional_mulcopy_coordinates}
        lua,_,_,system=registered_providers(overrides={"graphics":pixels,"graphics_controls":profile})
        providers,state=system["providers"],system["state"]
        vm=lua.table()
        calls={}
        def fixture(depth,mask=False,same=False):
            oracle.cursor=oracle.DATA+0x30000
            oracle.registered_pages=[];oracle.write_u32(oracle.page_head,0)
            assert pixels("reset")=="OK"
            state["pages"]=lua.table();state["next_page"]=1
            pages=[];ids=[]
            for index in range(2):
                data=bytes(rng.randrange(256)if depth==8 else
                           (255 if at%4==3 else rng.randrange(256))
                           for at in range(40*(depth//8)))
                masks=bytes(rng.randrange(256)for _ in range(40))if mask and depth==32 else None
                pages.append(oracle.page(8,5,data,depth,masks))
                ident=providers["$page_create_withalfa"if masks else"$page_create"](
                    vm,lua.table_from([8,5,0 if depth==8 else 1]))
                ids.append(ident)
                for at in range(40):
                    if depth==8:color=data[at]
                    else:
                        b,g,r,_=data[at*4:at*4+4];color=r+g*256+b*65536
                    assert pixels(f"set {ident} {at%8} {at//8} {color}")=="OK"
                    if masks:
                        mid=state["pages"][ident]["antidata"]
                        assert pixels(f"set {mid} {at%8} {at//8} {masks[at]}")=="OK"
            if same:pages[1]=pages[0];ids[1]=ids[0]
            return pages,ids

        def compare(pages,ids):
            for page,ident in zip(pages,ids):
                raw=bytes(oracle.machine.mem_read(page["pixels"],page["size"]))
                depth=page["depth"]
                mask=bytes(oracle.machine.mem_read(page["mask"]["pixels"],40))if page["mask"]else bytes(40)
                for at in range(40):
                    actual=tuple(map(int,pixels(f"pixel {ident} {at%8} {at//8}").split()[1:]))
                    if depth==8:
                        want=(raw[at],raw[at],raw[at],255)
                    else:
                        bpp=depth//8
                        want=(*raw[at*bpp:at*bpp+3][::-1],255-mask[at])
                    assert actual==want,(sample["sample"],at,ident,actual,want)

        for name in("$grp_mulcopy","$make_mosaic","$grp_overlayboxfill"):
            if name not in oracle.entries:continue
            call={"name":name,"hash":oracle.entries[name]["hash"],"cases":0,"failures":0,
                  "status":"passed","argument_counts":set(),"discard_modes":[False,True]}
            calls[name]=call
            for depth in(8,32):
                for masked in(False,True)if depth==32 else(False,):
                    for same in(False,True)if name!="$grp_overlayboxfill"else(False,):
                        for rectangle in rectangles:
                            for discard in(False,True):
                                pages,ids=fixture(depth,masked,same)
                                sx,sy,w,h,dx,dy=rectangle
                                if name=="$grp_overlayboxfill":
                                    color=rng.choice([0,0x01017f,0x7f8081,0xfefeff,0xffffff,0x123456])
                                    original=[pages[1]["page"],sx,sy,w,h,color]
                                    actual=[ids[1],sx,sy,w,h,color]
                                else:
                                    original=[pages[0]["page"],sx,sy,w,h,pages[1]["page"],dx,dy]
                                    actual=[ids[0],sx,sy,w,h,ids[1],dx,dy]
                                    if name=="$make_mosaic":
                                        block=rng.choice([1,2,3,4,9,33])
                                        original.append(block);actual.append(block)
                                    elif rectangle in rectangles[:3]and oracle.optional_mulcopy_coordinates:
                                        size=6 if dx==0 and dy==0 else 7
                                        original=original[:size];actual=actual[:size]
                                seed=rng.randrange(2**32)
                                oracle.seed(seed);state["random_seed"]=seed
                                try:
                                    expected=oracle.call_graphics(name,original,discard)
                                    returned=providers[name](vm,lua.table_from(actual))
                                    assert expected==([]if discard else[(0,returned)]),(name,expected,returned)
                                    compare(pages,ids)
                                    if name=="$make_mosaic":
                                        assert state["random_seed"]==oracle.random_seed(),(
                                            "mosaic CRT stream mismatch",state["random_seed"],oracle.random_seed())
                                except Exception as error:
                                    call["failures"]+=1;call["status"]="failed"
                                    if len(report["failures"])<24:
                                        report["failures"].append({"sample":sample["sample"],"name":name,
                                            "depth":depth,"masked":masked,"same":same,
                                            "rectangle":rectangle,"argc":len(actual),"reason":repr(error)})
                                call["cases"]+=1;call["argument_counts"].add(len(actual))
            call["argument_counts"]=sorted(call["argument_counts"])
        report["samples"].append({"sample":sample["sample"],"exe_sha256":sample["exe_sha256"],
                                   "graphics_profile":profile,
                                   "calls":list(calls.values())})
        failures=sum(call["failures"]for call in calls.values())
        print(f"{sample['sample']}: {sum(call['cases']for call in calls.values())} graphics cases; "
              f"{failures} mismatches",flush=True)
        write_json(folder/(folder.name[:8]+"_graphics_progress.json"),report)
    # Source release cannot delete dependencies of the saved target. Seeded
    # mosaic replay must recreate both its pixels and its CRT stream result.
    compact=lua.execute((ROOT/"player/runtime/majiro_graphics_checkpoint.lua").read_text(encoding="utf-8"))["compact"]
    commands=["create 1 4 4 0","fill 1 0 0 4 4 123456","create 2 4 4 0",
              "fill 2 0 0 4 4 654321","mosaic 1 0 0 4 4 2 0 0 3 1729",
              "overlayfill 2 0 0 4 4 765432","release 1"]
    assert pixels("reset")=="OK"
    for command in commands:assert pixels(command).startswith("OK")
    expected=[pixels(f"pixel 2 {at%4} {at//4}")for at in range(16)]
    pruned=list(compact(lua.table_from(commands),lua.table_from({2:{"width":4,"height":4,"depth":32}},
                                                            recursive=True)).values())
    assert commands[0]in pruned and commands[1]in pruned and commands[4]in pruned
    assert pixels("reset")=="OK"
    for command in pruned:assert pixels(command).startswith("OK")
    assert expected==[pixels(f"pixel 2 {at%4} {at//4}")for at in range(16)]
    report["checkpoint_seeded_replay"]=True
    portable.close()
    report["comparisons"]=sum(call["cases"]for row in report["samples"]for call in row["calls"])
    report["mismatches"]=sum(call["failures"]for row in report["samples"]for call in row["calls"])
    report["test_sources"]={name:sha256(ROOT/"scripts/analysis"/name)for name in(
        "original_opcode.py","original_strings.py","original_calls.py","native_function.py",
        "original_graphics_remaining.py","test_graphics_remaining.py","test_screen_semantics.py",
        "portable_pixels_probe.cpp")}
    report["source_changed_during_test"]=source_identity()!=before
    report["status"]="passed"if not report["mismatches"]and not report["source_changed_during_test"]else"failed"
    write_json(folder/(folder.name[:8]+"_graphics_remaining.json"),report)
    write_json(ROOT/"reports/graphics_remaining_tests.json",report)
    return 0 if report["status"]=="passed"else 1


if __name__=="__main__":raise SystemExit(run())
