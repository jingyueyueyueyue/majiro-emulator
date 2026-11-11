"""Compare terminal and interpolated sprite transforms with original x86."""
import json
from pathlib import Path
import struct
import sys

from audit_player_semantics import registered_providers
from native_function import NativeFunctions
from original_x86 import Original
from test_new_graphics_abi import FunctionFixture
from recover_happy_wrapper import invoke

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "convert"))
from port_common import ROOT, archive_dir, timestamp, write_json


def run():
    sample=next(row for row in json.loads((ROOT/"reports/opcode_dispatch.json").read_text(encoding="utf8"))["samples"]
                if row["exe_sha256"]==Original.IDENTITY)
    report={"status":"passed","timestamp":timestamp(),"cases":[],"failures":[]}
    for name,initializer,updater,offset,active in [
        ("xscale",0x473530,0x470840,0x3e4,0x3e0),
        ("yscale",0x4737b0,0x470af0,0x504,0x500),
        ("anti",0x4715f0,0x4701c0,0x2c,0x27c)]:
        for points in ([2.0,-1],[2.0,10,4.0,-1],[2.0,10,4.0,10],[0.5,0,3.0,-2])if name!="anti"else(
            [128,-1],[128,10,240,-1],[128,10,240,10],[32,0,255,-2]):
            f=FunctionFixture(sample);sprite=0x10050000;clock=[1000]
            f.put(0x5bab28,sprite);f.put(sprite+0x38,1);f.put(sprite+offset,77 if name=="anti"else 0x3f800000)
            f.hook(0x44ec90,lambda sp:f.top)
            f.hook(0x44eab0,lambda sp:0)
            f.hook(0x43a770,lambda sp:clock[0])
            encoded=[int.from_bytes(struct.pack("<f",value),"little")if i%2==0 and name!="anti"else value
                     for i,value in enumerate(points)]
            f.call(initializer,[sprite,*encoded])
            lua,_,_,system=registered_providers(overrides={"now":lambda:clock[0]})
            state=system["state"]
            state["pages"]=lua.table_from({1:{"width":8,"height":8},2:{"width":8,"height":8}},recursive=True)
            state["frontbuffer"]=2
            vm=lua.table();vm["frame"]=lambda self:None
            ident=system["providers"]["$sprite_create"](vm,lua.table_from([1]))
            if name=="anti":state["sprites"][ident]["anti"]=77
            provider="$sprite_alfa_define"if name=="anti"else"$sprite_"+("x"if name=="xscale"else"y")+"modify_define"
            system["providers"][provider](
                vm,lua.table_from([ident,*points]))
            values=[]
            for elapsed in (0,1,5,9,10,11,19,20,21,25,100):
                clock[0]=1000+elapsed
                invoke(f,updater,[sprite]);system["sprites"]["update"]()
                expected=f.read(sprite+offset)if name=="anti"else struct.unpack("<f",struct.pack("<I",f.read(sprite+offset)))[0]
                actual=state["sprites"][ident][name]
                values.append({"time":elapsed,"expected":expected,"actual":actual,
                               "original_active":f.read(sprite+active)})
                if abs(expected-actual)>1e-6:
                    report["failures"].append({"field":name,"points":points,**values[-1]})
            report["cases"].append({"field":name,"points":points,"samples":values})
    if report["failures"]:report["status"]="failed"
    folder=archive_dir("sprite_interpolation")
    write_json(folder/"report.json",report);write_json(ROOT/"reports/player_sprite_interpolation_tests.json",report)
    print(report["status"],len(report["failures"]),json.dumps(report["failures"][:5]),flush=True)
    return report


if __name__=="__main__":run()
