"""Original native picker initialization, empty-record guard and recent mapping."""
from pathlib import Path
import struct
import sys

ROOT=Path(__file__).resolve().parents[2]
sys.path[:0]=[str(ROOT/"scripts"/n)for n in("analysis","convert")]
import unicorn
from unicorn import x86_const as reg
from original_opcode import OpcodeOracle
from port_common import read_json,write_json,archive_dir,timestamp


def oracle_for(sample):
    assert sample["exe_sha256"]=="371fc97d0500b2bb20d23a82e604630d52ec9eb994d7bf32aba34eeae08571f1"
    return OpcodeOracle(sample)


def selection_guard(sample,loading,saved):
    oracle=oracle_for(sample);uc=oracle.machine;events=[]
    get_item,enable=oracle.DATA+0xe0000,oracle.DATA+0xe0010
    oracle.write_u32(0x47a2ec,get_item);oracle.write_u32(0x47a304,enable)
    def api(uc,at,size,unused):
        esp=uc.reg_read(reg.UC_X86_REG_ESP)
        count=2 if at in(get_item,enable)else 1
        args=[oracle.read_u32(esp+4+i*4)for i in range(count)]
        events.append({"api":"GetDlgItem"if at==get_item else"EnableWindow"if at==enable else"preview",
                       "args":args})
        uc.reg_write(reg.UC_X86_REG_EAX,args[1]if at==get_item else 1)
        uc.reg_write(reg.UC_X86_REG_EIP,oracle.read_u32(esp))
        uc.reg_write(reg.UC_X86_REG_ESP,esp+4+(count*4 if at!=0x422ff0 else 0))
    for at in(get_item,enable,0x422ff0):
        uc.hook_add(unicorn.UC_HOOK_CODE,api,begin=at,end=at)
    oracle.write_u32(0x518c1c,int(loading));oracle.write_u32(0x518c08,101)
    oracle.write_u32(0x518c38,0xffffffff)
    esp=oracle.STACK+0x7000;uc.mem_write(esp+0xa8,(b"saved"if saved else b"")+b"\0")
    uc.reg_write(reg.UC_X86_REG_ESP,esp);uc.reg_write(reg.UC_X86_REG_ESI,0x1234)
    uc.reg_write(reg.UC_X86_REG_EBX,1)
    uc.emu_start(0x422edb,0x422f51,count=20000,timeout=1000000)
    assert uc.reg_read(reg.UC_X86_REG_EIP)==0x422f51
    enabled=not loading or saved
    assert({"api":"EnableWindow","args":[1,1]}in events)==enabled
    assert oracle.read_u32(0x518c38)==(101 if enabled else 0xffffffff)
    return{"loading":loading,"record_exists":saved,"enabled":enabled,"events":events}


def initialization(sample):
    oracle=oracle_for(sample);uc=oracle.machine;events=[]
    targets={"GetDlgItem":oracle.DATA+0xe0000,"EnableWindow":oracle.DATA+0xe0010,
             "DestroyWindow":oracle.DATA+0xe0020,"SetTimer":oracle.DATA+0xe0030}
    counts={"GetDlgItem":2,"EnableWindow":2,"DestroyWindow":1,"SetTimer":4}
    def api(uc,at,size,unused):
        name=next(n for n,v in targets.items()if v==at)
        esp=uc.reg_read(reg.UC_X86_REG_ESP);count=counts[name]
        args=[oracle.read_u32(esp+4+i*4)for i in range(count)]
        events.append({"api":name,"args":args})
        uc.reg_write(reg.UC_X86_REG_EAX,args[1]if name=="GetDlgItem"else 1)
        uc.reg_write(reg.UC_X86_REG_EIP,oracle.read_u32(esp))
        uc.reg_write(reg.UC_X86_REG_ESP,esp+4+count*4)
    for name,at in targets.items():uc.hook_add(unicorn.UC_HOOK_CODE,api,begin=at,end=at)
    for iat,name in((0x47a304,"EnableWindow"),(0x47a378,"DestroyWindow"),(0x47a300,"SetTimer")):
        oracle.write_u32(iat,targets[name])
    stop,esp=oracle.DATA+0xff000,oracle.STACK+0x8000
    uc.mem_write(esp,struct.pack("<5I",stop,0x1234,0x110,0,0))
    uc.reg_write(reg.UC_X86_REG_ESP,esp-0x4a8);uc.reg_write(reg.UC_X86_REG_EBX,1)
    uc.reg_write(reg.UC_X86_REG_ESI,0x1234);uc.reg_write(reg.UC_X86_REG_EBP,targets["GetDlgItem"])
    uc.emu_start(0x422a27,stop,count=10000,timeout=1000000)
    assert uc.reg_read(reg.UC_X86_REG_EIP)==stop
    assert{"api":"EnableWindow","args":[1,0]}in events
    assert oracle.read_u32(0x518c38)==0xffffffff
    return{"selected":-1,"enabled":False,"events":events}


def recent_mapping(sample):
    oracle=oracle_for(sample);uc=oracle.machine;rows=[]
    for visual in range(1101,1111):
        oracle.write_u32(0x518c10,1101);uc.reg_write(reg.UC_X86_REG_EDI,visual-1101)
        uc.emu_start(0x422d12,0x422db4,count=10000,timeout=1000000)
        assert uc.reg_read(reg.UC_X86_REG_EIP)==0x422db4
        actual=oracle.read_u32(0x518c08)
        expected=10001 if visual==1101 else visual if visual==1102 else visual+8900
        assert actual==expected
        rows.append({"visual":visual,"canonical":actual,"selectable":visual!=1102})
    return rows


def main():
    folder=archive_dir("native_slot_original")
    sample=next(s for s in read_json(ROOT/"reports/opcode_dispatch.json")["samples"]
                if s["sample"]=="happy_princess")
    report={"timestamp":timestamp(),"status":"running","device_accessed":False,
        "exe_sha256":sample["exe_sha256"],"scope":"original initialization/confirmation guard/recent-slot remapping; UI APIs and thumbnail resource read are explicit fixture boundaries",
        "evidence":str(folder.relative_to(ROOT))}
    try:
        report["initialization"]=initialization(sample)
        report["selection"]=[selection_guard(sample,*args)for args in((True,False),(True,True),(False,False))]
        report["recent_mapping"]=recent_mapping(sample);report["status"]="passed"
    except Exception as error:
        report.update(status="failed",error=str(error));raise
    finally:
        write_json(folder/"report.json",report);write_json(ROOT/"reports/native_slot_original.json",report)
    print("Original native picker: initialization, 3 selection guards and 10 recent-slot mappings passed")


if __name__=="__main__":main()
