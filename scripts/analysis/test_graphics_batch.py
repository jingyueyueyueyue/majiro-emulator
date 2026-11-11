"""Small native checks for newly implemented graphics-family entrypoints."""
from __future__ import annotations
import json
from pathlib import Path
import struct
import sys

from native_pixels import Pixels
from original_graphics_remaining import GraphicsOracle
from audit_player_semantics import registered_providers

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/"convert"))
from port_common import ROOT,archive_dir,timestamp,write_json


def run():
    folder=archive_dir("graphics_batch_checks");pixels=Pixels()
    from original_x86 import Original
    sample=next(row for row in json.loads((ROOT/"reports/opcode_dispatch.json").read_text(encoding="utf8"))["samples"]
                if row["exe_sha256"]==Original.IDENTITY)
    entries=next(row["entries"] for row in json.loads((ROOT/"reports/syscall_dispatch.json").read_text(encoding="utf8"))["samples"]
                 if row["exe_sha256"]==Original.IDENTITY)
    cases=[]
    for source_mask,dest_mask in((False,False),(True,False),(False,True),(True,True)):
        o=GraphicsOracle(sample,entries);source=bytes((30,60,90,255))*256;dest=bytes((60,90,120,255))*256
        s=o.page(16,16,source,mask=bytes([100])*256 if source_mask else None)
        d=o.page(16,16,dest,mask=bytes([200])*256 if dest_mask else None)
        c=o.page(16,16,bytes(range(256)),8)
        table=[(value<<24)|0x112233 for value in range(256)]
        array=o.memory(struct.pack("<5I",64,0,1,1,256)+struct.pack("<256I",*table))
        entry=o.entries["$fuchidori_copy"]
        args=[(0,s["page"]),(0,d["page"]),(0,c["page"]),(0,0),(0,0),(3,array)]
        result=o.execute("syscall",list(reversed(args)),struct.pack("<IH",int(entry["hash"],16),6),budget=1_000_000)
        assert result==[(0,0)]
        pixels("reset")
        for id,color in((1,1981530),(2,3955320)):
            assert pixels(f"create {id} 16 16 0")=="OK"
            assert pixels(f"fill {id} 0 0 16 16 {color}")=="OK"
        pixels("create 3 16 16 0 8")
        if source_mask:pixels("alpha 1 4 0");pixels("fill 4 0 0 16 16 100")
        if dest_mask:pixels("alpha 2 5 0");pixels("fill 5 0 0 16 16 200")
        for at in range(256):pixels(f"set 3 {at%16} {at//16} {at}")
        assert pixels("fuchidori 1 2 3 0 0 "+" ".join(map(str,table)))=="OK"
        raw=bytes(o.machine.mem_read(d["pixels"],1024))
        mask=bytes(o.machine.mem_read(d["mask"]["pixels"],256))if dest_mask else bytes(256)
        for at in range(256):
            actual=tuple(map(int,pixels(f"pixel 2 {at%16} {at//16}").split()[1:]))
            expected=(*raw[at*4:at*4+3][::-1],255-mask[at])
            assert actual==expected,(source_mask,dest_mask,at,actual,expected)
        cases.append({"operation":"fuchidori","source_mask":source_mask,"destination_mask":dest_mask,
                      "table_modes":256})
    pixels("reset")
    lua,_,_,system=registered_providers(overrides={"graphics":pixels,"now":lambda:125})
    vm=lua.eval("function()return{frame=function()return{script={name='fixture'}}end}end")()
    call=lambda name,*args:system["providers"][name](vm,lua.table_from(args,recursive=True))
    page=call("$page_create",8,4)
    call("$grp_boxfill",page,0,0,8,4,0x123456)
    sprite=call("$sprite_create",page,0,0,4,4)
    call("$sprite_animate_define",sprite,0,100,1,100)
    call("$sprite_set_smooth_animation",sprite)
    call("$sprite_tl_set",sprite,64)
    call("$sprite_alfa_define_abs",sprite,255,100,0,100)
    assert system["state"]["sprites"][sprite]["smooth_page"]
    dest=call("$page_create",4,2)
    call("$make_thumbnail",page,0,0,8,4,dest,0,0,4,2)
    assert pixels(f"get {dest} 0 0")=="OK 1193046"
    table=lua.table_from({"kind":"array","element_type":0,"dims":[256],
                         "values":{i:i for i in range(256)}},recursive=True)
    control=call("$page_create",8,4,0)
    system["providers"]["$grp_alfa_tabled_copy"](vm,lua.table_from(
        [page,0,0,4,2,dest,0,0,control,table]))
    cases.append({"operation":"six_graphics_and_sprite_providers","status":"entered"})
    report={"schema":1,"timestamp":timestamp(),"status":"passed","cases":cases,
            "exe_sha256":sample["exe_sha256"],"scope":"1024 native fuchidori pixels plus new provider entry smoke checks",
            "full_semantics_complete":False}
    write_json(folder/(folder.name[:8]+"_graphics_batch.json"),report)
    write_json(ROOT/"reports/graphics_batch_tests.json",report)
    print("Graphics batch: 1024 native pixels and six provider entries passed")


if __name__=="__main__":run()
