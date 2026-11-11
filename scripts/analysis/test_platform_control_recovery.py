"""Recover and verify platform-control families across original engine builds."""
from __future__ import annotations

from collections import Counter
import hashlib
import json
from pathlib import Path
import sys

from original_platform_controls import PlatformOracle,HASHES

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/"convert"))
from port_common import ROOT,archive_dir,timestamp,write_json,atomic_bytes,lua_value


def cases(oracle):
    for hash_ in("2bd1709d","7e0b8320"):
        if hash_ in oracle.roots:
            for size in((800,600),(1280,720),(3,7)):
                yield hash_,(),{"size":size}
    for value in(-2147483648,-1,0,1,7,2147483647):
        for raw in(b"",b"fixture/path.rct",bytes.fromhex("8365835883672e726374"),bytes(range(128,256))):
            yield "42d7c922",(value,),{"drop_bytes":raw}
    for raw in(b"",b"fixture/path.rct",bytes.fromhex("8365835883672e726374"),bytes(range(128,256)),b"x"*259):
        yield "c32e286f",(),{"drop_bytes":raw}
    for home in((0,0),(3,-2),(-4,7)):
        for enabled,success in((0,True),(1,False),(1,True)):
            for point in((-1,0),(0,-1),(0,0),(799,599),(800,600),(801,601),(805,599)):
                yield "3670b283",(),{"enabled":enabled,"success":success,"point":point,"home":home}
    for hash_ in("05b1a6e8","d5999d46"):
        if hash_ in oracle.roots:
            for disabled in(0,1,7):
                for caps in(0,1):yield hash_,(),{"keyboard_disabled":disabled,"caps":caps}
    if "929a367d"in oracle.roots:
        for caps in(0,1,0x8000,0x8001,0xffff):yield "929a367d",(),{"caps":caps}
    if "125cfbdb"in oracle.roots:
        for guard in(-1,0,7):
            for caps in(0,1,0x8000,0x8001):
                for value in(-1,0,1,7):yield "125cfbdb",(value,),{"caps_guard":guard,"caps":caps}
    for home in((0,0),(3,-2),(-4,7)):
        for point in((0,0),(17,-21),(-10000,25),(2147483647,-2147483648)):
            yield "d4590bd3",point,{"home":home}


def expected_value(oracle,hash_,args,fixture):
    if hash_=="2bd1709d":return fixture["size"][1]
    if hash_=="7e0b8320":return fixture["size"][0]
    if hash_=="c32e286f":return fixture["drop_bytes"].hex()
    if hash_=="3670b283":
        if not fixture.get("enabled",1)or not fixture.get("success",True):return 0
        x,y=fixture["point"];hx,hy=fixture["home"];w,h=800,600
        if oracle.containment_mode=="legacy_exclusive":return int(x>=0 and y>=0 and x<w and y<h)
        if oracle.containment_mode=="fixed_offset_inclusive":x+=hx;y+=hy
        else:w+=2*hx;h+=2*hy
        return int(x>=0 and y>=0 and x<=w and y<=h)
    if hash_=="d5999d46":return int(fixture["keyboard_disabled"]==0)
    if hash_=="929a367d":return fixture["caps"]&1
    return 0


def run():
    samples=json.loads((ROOT/"reports/opcode_dispatch.json").read_text(encoding="utf8"))["samples"]
    entries={s["sample"]:s["entries"]for s in
        json.loads((ROOT/"reports/syscall_dispatch.json").read_text(encoding="utf8"))["samples"]}
    report={"schema":1,"timestamp":timestamp(),"samples":[],"failures":[],
        "scope":"original platform state, raw drop bytes, inclusive/exclusive cursor containment, "
                "caps request guard, keyboard buffer clear and physical cursor request boundary",
        "host_boundaries":["fixture client rectangle/cursor/key state","Win32 effects recorded without executing",
                           "single-thread locks","GS cookie verification"],"runtime_implemented":False}
    for sample in samples:
        oracle=PlatformOracle(sample,entries[sample["sample"]]);profile=oracle.profile()
        counts=Counter();failures=Counter()
        for hash_,args,fixture in cases(oracle):
            for discard in(False,True):
                counts[hash_]+=1
                try:
                    got=oracle.call_control(hash_,args,discard,**fixture)
                    expected=[]if discard else[expected_value(oracle,hash_,args,fixture)]
                    assert got["result"]==expected,("result",got,expected)
                    if hash_=="42d7c922":
                        assert got["drag_flag"]==args[0]
                        assert got["drop_bytes"]==(""if args[0]else fixture["drop_bytes"].hex())
                    if hash_=="05b1a6e8":
                        assert got["keyboard_disabled"]==0 and got["keyboard_buffers_cleared"]
                    if hash_=="d5999d46":assert got["keyboard_disabled"]==1
                    if hash_=="125cfbdb":
                        events=[r for r in got["platform_calls"]if r["api"]=="keybd_event"]
                        change=fixture["caps_guard"]==-1 and bool(fixture["caps"]&1)!=bool(args[0])
                        assert len(events)==(2 if change else 0)
                    if hash_=="d4590bd3":
                        cx,cy=oracle.axes[0],oracle.axes[1]
                        # Emulated client origin is zero; final OS conversion
                        # adds the fixed fixture desktop origin.
                        hx,hy=fixture["home"];x,y=args
                        signed=lambda v:((v+2147483648)%4294967296)-2147483648
                        def trunc(v):return int(v)
                        if profile["mouse_move"]=="legacy_identity":lx,ly=x,y
                        elif profile["mouse_move"]=="modern":
                            converted=lambda v:-2147483648 if v>=2147483648 or v< -2147483648 else int(v)
                            lx,ly=converted(x+hx),converted(y+hy)
                        else:
                            direction=1 if profile["mouse_move"]=="fixed_plus"else-1
                            lx=signed(trunc(signed(x*1000)/1000)+direction*hx)
                            ly=signed(trunc(signed(y*1000)/1000)+direction*hy)
                        expected_move=(signed(lx+41),signed(ly-23))
                        assert got["moved"]==expected_move,(got["moved"],expected_move)
                except Exception as error:
                    failures[hash_]+=1
                    if len(report["failures"])<30:report["failures"].append({
                        "sample":sample["sample"],"hash":hash_,"args":args,
                        "fixture":{k:v.hex()if isinstance(v,bytes)else v for k,v in fixture.items()},
                        "discard":discard,"reason":str(error)})
        report["samples"].append({"sample":sample["sample"],"exe_sha256":sample["exe_sha256"],
            "profile":profile,"counts":dict(counts),"failures":dict(failures),
            "entries":{h:oracle.hash_entries[h]["selected_va"]for h in HASHES if h in oracle.hash_entries}})
        print(sample["sample"],sum(counts.values()),"native platform cases;",
              sum(failures.values()),"mismatches",flush=True)
    report["comparisons"]=sum(sum(s["counts"].values())for s in report["samples"])
    report["mismatches"]=sum(sum(s["failures"].values())for s in report["samples"])
    report["status"]="passed"if not report["mismatches"]else"failed"
    report["test_sources"]={name:hashlib.sha256((ROOT/"scripts/analysis"/name).read_bytes()).hexdigest()
        for name in("original_platform_controls.py","test_platform_control_recovery.py",
                    "original_screen.py","original_calls.py","original_strings.py","original_opcode.py")}
    folder=archive_dir("platform_control_recovery")
    write_json(folder/(folder.name[:8]+"_platform_control_recovery.json"),report)
    write_json(ROOT/"reports/platform_control_recovery.json",report)
    if report["status"]=="passed":
        mapping={row["exe_sha256"]:row["profile"]for row in report["samples"]}
        extra=ROOT/"reports/new_engine_profiles.json"
        if extra.exists():mapping.update(json.loads(extra.read_text(encoding="utf8")).get("platform_executables",{}))
        atomic_bytes(ROOT/"player/runtime/majiro_platform_profiles.lua",(
            "-- Recovered original platform behavior; keyed by executable SHA-256.\nreturn "+
            lua_value(mapping)+"\n").encode("utf8"))
    return 0 if report["status"]=="passed"else 1


if __name__=="__main__":raise SystemExit(run())
