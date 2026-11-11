"""Focused original draws and generated opponent-recipe comparisons."""
from __future__ import annotations
import json
from pathlib import Path
import struct
import sys
import unicorn
from unicorn import x86_const as reg
from audit_player_semantics import registered_providers
from original_graphics_remaining import GraphicsOracle
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/"convert"))
from port_common import ROOT,archive_dir,timestamp,write_json


def run():
    folder=archive_dir("tile_actions")
    sample=json.loads((ROOT/"reports/opcode_dispatch.json").read_text(encoding="utf8"))["samples"][-1]
    entries=json.loads((ROOT/"reports/syscall_dispatch.json").read_text(encoding="utf8"))["samples"][-1]["entries"]
    rows=[]
    for players,character in((2,1),(3,2),(4,3)):
        oracle=GraphicsOracle(sample,entries)
        translated=[]
        lua,_,_,system=registered_providers(overrides={"tile_trace":lambda *args:translated.append(args)})
        api=system["state"]["tiles_actions"]
        seed=178;oracle.seed(seed);system["state"]["random_seed"]=seed
        def put(at,value):
            oracle.write_u32(at,value);api["put"](at,value)
        def get(at):return struct.unpack("<i",oracle.machine.mem_read(at,4))[0]
        put(0x5d1578,players);put(0x5d1580,0)
        put(0x55e204,0)
        for player in range(players-1):
            put(0x5d0a10+player*4,character);put(0x5d0a00+player*4,3)
        wall=[]
        for kind in range(1,38):
            if kind%10:
                wall.extend([kind*10+copy for copy in range(4)])
        if players==3:wall=wall[:108]
        for i,value in enumerate(wall):put(0x5d1800+i*4,value);put(0x5d15a0+i*4,0)
        for kind in range(40):put(0x5b4480+kind*4,4);put(0x5b4520+kind*4,4)
        touched=set()
        native=[]
        data=lua.execute((ROOT/"player/tables/tile_ai.lua").read_text(encoding="utf8"))
        valid=set(data["instructions"].keys())
        def code(uc,address,size,unused):
            if address in valid:native.append((address,*[uc.reg_read(r)for r in(
                reg.UC_X86_REG_EAX,reg.UC_X86_REG_EBX,reg.UC_X86_REG_ECX,reg.UC_X86_REG_EDX,
                reg.UC_X86_REG_ESI,reg.UC_X86_REG_EDI,reg.UC_X86_REG_ESP,reg.UC_X86_REG_EBP)]))
        oracle.machine.hook_add(unicorn.UC_HOOK_CODE,code)
        def changed(uc,access,address,size,value,unused):
            if 0x4c7000<=address<0x600000:
                for at in range(address-address%4,address+size,4):touched.add(at)
        hook=oracle.machine.hook_add(unicorn.UC_HOOK_MEM_WRITE,changed)
        uc=oracle.machine;uc.context_restore(oracle.initial)
        stack=oracle.STACK+0xf000
        uc.mem_write(stack,struct.pack("<I",oracle.STOP))
        uc.reg_write(reg.UC_X86_REG_ESP,stack);uc.reg_write(reg.UC_X86_REG_EBP,0)
        uc.reg_write(reg.UC_X86_REG_EFLAGS,2)
        uc.emu_start(0x4326c0,oracle.STOP,count=2_000_000)
        assert uc.reg_read(reg.UC_X86_REG_EIP)==oracle.STOP,"original AI instruction budget"
        oracle.machine.hook_del(hook)
        api["run_ai"]()
        mismatches=[]
        for at in sorted(touched):
            actual=api["get"](at);expected=get(at)
            if actual!=expected:mismatches.append((hex(at),actual,expected))
        if mismatches:
            compact=[]
            for row in native:
                if compact and row[0]==compact[-1][0]and data["instructions"][row[0]][1]=="rep movsd":continue
                compact.append(row)
            native=compact
            first=next((i for i,(a,b)in enumerate(zip(native,translated))if a[0]!=b[0]),None)
            if first:
                print("PC divergence",first,native[max(0,first-3):first+2],translated[max(0,first-3):first+2])
            print("seeds",system["state"]["random_seed"],oracle.random_seed())
            write_json(folder/"20261110_trace.json",{"native":native,"translated":translated})
        assert not mismatches,(players,character,mismatches[:10])
        assert system["state"]["random_seed"]==oracle.random_seed()
        rows.append({"players":players,"character":character,"native_written_fields":len(touched),
                     "translated_steps":system["state"]["tiles"]["last_ai_steps"]})
        print(players,character,len(touched),"tile AI state fields passed",flush=True)
    report={"schema":1,"timestamp":timestamp(),"status":"passed","cases":rows,
        "scope":"original pure tile-AI function and generated integer interpreter, memory writes and RNG",
        "limits":["other character recipes and entire tile-game session remain final validation targets"]}
    write_json(folder/(folder.name[:8]+"_tile_actions.json"),report)
    write_json(ROOT/"reports/tile_action_tests.json",report)


if __name__=="__main__":run()
