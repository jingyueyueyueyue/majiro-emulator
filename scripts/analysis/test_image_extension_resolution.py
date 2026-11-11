"""Execute original image filename selection with memory-only file probes."""
from __future__ import annotations
import ntpath
import struct
from test_new_graphics_abi import FunctionFixture
from unicorn import x86_const as reg
from pathlib import Path
import sys
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/"convert"))
from port_common import ROOT,archive_dir,read_json,timestamp,write_json


def main():
    sample=next(s for s in read_json(ROOT/"reports/opcode_dispatch.json")["samples"]if s["sample"]=="BOKUTSUBA")
    rows=[]
    for requested,available,expected in [
        ("click.png",["click.rct"],"click.rct"),
        ("click.rc8",["click.png","click.rct","click.rc8"],"click.png"),
        ("click.rct",["click.bmp","click.rct"],"click.bmp"),
        ("click",["click.rc8"],"click.rc8"),
        ("click.png",[],"click"),
        ("クリック待ちアイコン.png",["クリック待ちアイコン.rct"],"クリック待ちアイコン.rct"),
    ]:
        f=FunctionFixture(sample);f.put(0x4efe68,0)
        f.uc.mem_write(0x4efc64,b"\xff"+bytes(259))
        def text(address):
            output=bytearray()
            for i in range(4096):
                value=bytes(f.uc.mem_read(address+i,1))
                if value==b"\0":return bytes(output)
                output.extend(value)
            raise ValueError("unterminated native fixture filename")
        def split(sp):
            value=text(f.read(sp+4)).decode("cp932")
            drive,tail=ntpath.splitdrive(value);directory,base=ntpath.split(tail);stem,ext=ntpath.splitext(base)
            for index,value in [(2,drive),(3,directory),(4,stem),(5,ext)]:
                pointer=f.read(sp+index*4)
                if pointer:f.uc.mem_write(pointer,value.encode("cp932")+b"\0")
            return 0
        def compare(sp):return 0 if text(f.read(sp+4)).lower()==text(f.read(sp+8)).lower()else 1
        def copy(sp):
            target=f.read(sp+4);f.uc.mem_write(target,text(f.read(sp+8))+b"\0");return target
        def concatenate(sp):
            target=f.read(sp+4);f.uc.mem_write(target,text(target)+text(f.read(sp+8))+b"\0");return target
        def formatting(sp):
            value=text(f.read(sp+8)).replace(b"%s",text(f.read(sp+12)))
            f.uc.mem_write(f.read(sp+4),value+b"\0");return len(value)
        attempts=[]
        def exists(sp):
            name=text(f.read(sp+4)).decode("cp932");attempts.append(name)
            return 1 if name in available else 0
        for at,fn in [(0x450d91,split),(0x450b66,compare),(0x450540,copy),
                      (0x450550,concatenate),(0x450630,formatting),
                      (0x446130,exists),(0x445c80,exists),(0x442df0,exists)]:
            f.hook(at,fn)
        source,out=0x10030000,0x10031000
        f.uc.mem_write(source,requested.encode("cp932")+b"\0")
        stack=0x2000f000;f.uc.mem_write(stack,struct.pack("<3I",0x30000000,source,out))
        f.uc.reg_write(reg.UC_X86_REG_ESP,stack);f.uc.reg_write(reg.UC_X86_REG_EFLAGS,2)
        f.uc.emu_start(0x4180f0,0x30000000,count=100000)
        assert f.uc.reg_read(reg.UC_X86_REG_EIP)==0x30000000
        actual=text(out).decode("cp932");assert actual==expected,(requested,actual,attempts)
        assert f.uc.reg_read(reg.UC_X86_REG_EAX)==(0 if available else 0xffffffff)
        rows.append({"requested":requested,"available":available,"resolved":actual,"probes":attempts,"status":"passed"})
    folder=archive_dir("image_extension_resolution")
    report={"timestamp":timestamp(),"status":"passed","cases":rows,"original_exe_sha256":sample["exe_sha256"],
        "original_resolver":"004180f0","scope":"original selector with explicit CRT and file-existence fixture boundaries",
        "rule":"logical basename selects PNG, BMP, RCT, RC8 in order, regardless of supplied image suffix",
        "device_accessed":False,"evidence":str(folder.relative_to(ROOT))}
    write_json(ROOT/"reports/image_extension_resolution.json",report);write_json(folder/"report.json",report)
    print("Image filename resolution:",len(rows),"original x86 cases passed")
    return 0


if __name__=="__main__":raise SystemExit(main())
