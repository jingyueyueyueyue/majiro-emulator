"""Original live-sprite page deletion guard and player auto-release lifetime."""
from pathlib import Path
import struct
import sys
import unicorn

from original_x86 import Original
from test_original_blend import original_page
from audit_player_semantics import registered_providers
from native_pixels import Pixels

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/"convert"))
from port_common import ROOT,archive_dir,timestamp,write_json


def oracle_release(attached,masked):
    oracle=Original();released=[];diagnostics=[]
    page,_,_=original_page(oracle,2,1,bytes((30,20,10,255))*2,b"\x40\x80"if masked else None)
    if masked:oracle.machine.mem_write(page+0x18,struct.pack("<I",1))
    sprite=oracle.memory(bytes(0x630));oracle.machine.mem_write(sprite,struct.pack("<I",page))
    oracle.machine.mem_write(0x5bab28,struct.pack("<I",sprite if attached else 0))
    def boundary(uc,address,size,unused):
        stack=uc.reg_read(oracle.reg.UC_X86_REG_ESP)
        if address==0x475340:released.append(int.from_bytes(oracle.read(stack+4,4),"little"))
        else:diagnostics.append(address)
        target=int.from_bytes(oracle.read(stack,4),"little")
        uc.reg_write(oracle.reg.UC_X86_REG_EAX,0)
        uc.reg_write(oracle.reg.UC_X86_REG_ESP,stack+4)
        uc.reg_write(oracle.reg.UC_X86_REG_EIP,target)
    for at in(0x475340,0x441150):oracle.machine.hook_add(unicorn.UC_HOOK_CODE,boundary,begin=at,end=at)
    oracle.syscall(0x44b510,[page])
    return {"freed_pages":len(released),"diagnostics":len(diagnostics),"source_freed":page in released}

def oracle_sprite_release(automatic,owned):
    oracle=Original();released=[]
    page,_,_=original_page(oracle,4,2,bytes((30,20,10,255))*8,bytes([80])*8)
    mask=int.from_bytes(oracle.read(page+0x20,4),"little")
    oracle.machine.mem_write(page+0x18,struct.pack("<I",int(owned)))
    sprite=oracle.memory(bytes(0x630))
    oracle.machine.mem_write(sprite,struct.pack("<I",page))
    oracle.machine.mem_write(sprite+0x20,struct.pack("<I",int(automatic)))
    oracle.machine.mem_write(0x5bab28,struct.pack("<I",sprite))
    def boundary(uc,address,size,unused):
        sp=uc.reg_read(oracle.reg.UC_X86_REG_ESP)
        if address==0x475340:released.append(int.from_bytes(oracle.read(sp+4,4),"little"))
        target=int.from_bytes(oracle.read(sp,4),"little")
        uc.reg_write(oracle.reg.UC_X86_REG_ESP,sp+4)
        uc.reg_write(oracle.reg.UC_X86_REG_EIP,target)
    for at in(0x475340,0x4712e0):
        oracle.machine.hook_add(unicorn.UC_HOOK_CODE,boundary,begin=at,end=at)
    oracle.syscall(0x4731f0,[sprite])
    return {"source_freed":page in released,"mask_freed":mask in released}


def run():
    rows=[]
    for attached in(False,True):
        for masked in(False,True):
            expected=oracle_release(attached,masked);commands=[]
            lua,_,_,system=registered_providers(overrides={"graphics":lambda command:commands.append(command)or"OK"})
            vm=lua.table();vm["frame"]=lambda self:None
            call=lambda name,*args:system["providers"][name](vm,lua.table_from(args))
            state=system["state"]
            page=call("$page_create_withalfa"if masked else"$page_create",2,1)
            mask=state["pages"][page]["antidata"]
            sprite=call("$sprite_create",page)if attached else None
            commands.clear();call("$page_release",page)
            actual=state["pages"][page]is None
            assert actual==expected["source_freed"],(attached,masked,expected,commands)
            if attached:
                assert not commands
                if masked:assert state["pages"][mask]is not None
                call("$sprite_release",sprite)
                assert state["pages"][page]is not None
                call("$page_release",page)
                assert state["pages"][page]is None
            rows.append({"sprite_attached":attached,"masked":masked,"original":expected})
    for masked in(False,True):
        commands=[]
        lua,_,_,system=registered_providers(overrides={"graphics":lambda command:commands.append(command)or"OK"})
        vm=lua.table();vm["frame"]=lambda self:None
        call=lambda name,*args:system["providers"][name](vm,lua.table_from(args))
        state=system["state"];page=call("$page_create_withalfa"if masked else"$page_create",4,2)
        mask=state["pages"][page]["antidata"]
        sprite=call("$sprite_create",page,0,0,2,2)
        call("$sprite_page_auto_release",sprite)
        call("$sprite_set_smooth_animation",sprite)
        temporary=state["sprites"][sprite]["smooth_page"]
        call("$page_release",page)
        assert state["pages"][page]is not None
        call("$sprite_release",sprite)
        assert state["pages"][page]is None and state["pages"][temporary]is None
        if masked:assert state["pages"][mask]is None
        rows.append({"auto_release":True,"smooth_page":True,"masked":masked})
    for automatic in(False,True):
        for owned in(False,True):
            expected=oracle_sprite_release(automatic,owned)
            for smooth in(False,True):
                native=Pixels()
                lua,_,_,system=registered_providers(overrides={"graphics":native})
                vm=lua.table();vm["frame"]=lambda self:None
                call=lambda name,*args:system["providers"][name](vm,lua.table_from(args))
                state=system["state"]
                page=call("$page_create_withalfa"if owned else"$page_create",4,2)
                if owned:mask=state["pages"][page]["antidata"]
                else:
                    mask=call("$page_create",4,2,0)
                    call("$page_set_antidata",page,mask)
                sprite=call("$sprite_create",page,0,0,2,2)
                if automatic:call("$sprite_page_auto_release",sprite)
                temporary=None
                if smooth:
                    call("$sprite_set_smooth_animation",sprite)
                    temporary=state["sprites"][sprite]["smooth_page"]
                call("$sprite_release",sprite)
                assert (state["pages"][page]is None)==expected["source_freed"]
                assert (state["pages"][mask]is None)==expected["mask_freed"]
                assert native(f"pixel {page} 0 0").startswith("ERR")==expected["source_freed"]
                assert native(f"pixel {mask} 0 0").startswith("ERR")==expected["mask_freed"]
                if temporary:
                    assert state["pages"][temporary]is None
                    assert native(f"pixel {temporary} 0 0").startswith("ERR")
                rows.append({"auto_release":automatic,"owned_mask":owned,"smooth_page":smooth,
                             "original":expected,"native_lifetime_verified":True})
    report={"status":"passed","timestamp":timestamp(),"cases":rows,
            "original_exe_sha256":Original.IDENTITY,
            "scope":"original explicit source-page release guard and owned/external alpha sprite destruction; real independent native handles"}
    folder=archive_dir("page_lifetimes")
    write_json(folder/"report.json",report);write_json(ROOT/"reports/player_page_lifetime_tests.json",report)
    print("page lifetimes:",len(rows),"passed",flush=True)
    return report


if __name__=="__main__":run()
