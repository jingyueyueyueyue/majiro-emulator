"""Translate recovered pure tile routines into a bounded portable operation table.

No EXE loading or x86 decoding occurs in the player. This conversion reads the
fixed original only during development and emits only recovered operations and
their constant data, plus named VM GLOBAL bindings.
"""
from __future__ import annotations
from collections import Counter,deque
import hashlib,json,re,struct,sys,zlib
from pathlib import Path
import capstone
import pefile

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/"analysis"))
from original_x86 import Original
from native_function import NativeFunctions
from port_common import ROOT,write_json,timestamp

ENTRY_HASHES=("aefc64da b758ac4e bb9d7a7f bef4edfa ca5c98f2 d481e1a2 d583668c "
              "da60f137 e316a8f2 e5a2ce14 ebb5886c f0fd9886 f5e5be93 faf17b1c "
              "fb7fb744 efd59130 0d36e2a7 1cd5bca8 1f5a14d4 23d5d11d "
              "2c18d5a9 2e99e98c 45e32ea6 50cd2dca 5c9761c5 62442ec6 "
              "6ddeb30e 850eecf4 269bda78").split()
BOUNDARIES={0x45B510:"sync_in",0x45C8E0:"sync_out",0x44EC90:"arguments",0x44EAB0:"result",
            0x48999C:"random",0x489C60:"copy",0x48AA80:"copy",0x48B0D0:"fill",
            0x48A437:"cookie",0x479370:"diagnostic",0x4326C0:"opponent"}


def literal(value):
    if isinstance(value,list):return"{"+",".join(literal(v)for v in value)+"}"
    if isinstance(value,str):return json.dumps(value,ensure_ascii=True)
    if value is None:return"false"
    return str(value)


def main():
    original=Original();functions=NativeFunctions(original)
    pe=pefile.PE(str(original.path))
    scan=json.loads((ROOT/"reports/syscall_dispatch.json").read_text())["samples"]
    entries=[row for row in next(row for row in scan if row["sample"]=="RURINOKASANE")["entries"]
             if row["hash"]in ENTRY_HASHES]
    roots={row["hash"]:functions.syscall_helper(row)for row in entries}
    pending=deque(roots.values());program={};calls=set();constants=set()
    ranges=[]
    for section in pe.sections:
        if section.Characteristics&0x20000000:
            ranges.append((0x400000+section.VirtualAddress,0x400000+section.VirtualAddress+section.Misc_VirtualSize))
    def executable(at):return any(lo<=at<hi for lo,hi in ranges)
    def operand(item):
        if item.type==capstone.CS_OP_REG:return["r",functions.md.reg_name(item.reg),item.size]
        if item.type==capstone.CS_OP_IMM:
            if 0x4A0000<=item.imm<0x5D2000:constants.add(item.imm)
            return["i",item.imm]
        if item.type==capstone.CS_OP_MEM:
            if 0x400000<=item.mem.disp<0x5D2000:constants.add(item.mem.disp)
            return["m",functions.md.reg_name(item.mem.base)if item.mem.base else"",
                    functions.md.reg_name(item.mem.index)if item.mem.index else"",item.mem.scale,item.mem.disp,item.size]
        raise ValueError("unknown recovered tile operand")
    while pending:
        at=pending.popleft()
        if at in program or at in BOUNDARIES:continue
        if not(0x45D000<=at<0x467C50 or 0x432360<=at<0x432378):
            raise ValueError(f"unrecovered tile function {at:08x}")
        row=functions.instruction(at)
        if row.mnemonic in("int3","int","hlt"):raise ValueError("tile recovery reached noncode")
        program[at]=[row.address+row.size,row.mnemonic,*[operand(item)for item in row.operands]]
        if row.mnemonic=="call":
            if row.operands[0].type!=capstone.CS_OP_IMM:raise ValueError("indirect tile call requires review")
            target=row.operands[0].imm;calls.add(target)
            pending.append(target);pending.append(at+row.size)
        elif row.mnemonic=="ret":pass
        elif row.mnemonic=="jmp":
            if row.operands[0].type==capstone.CS_OP_IMM:pending.append(row.operands[0].imm)
            else:
                op=row.operands[0]
                if op.type!=capstone.CS_OP_MEM or op.mem.base or not op.mem.index:
                    raise ValueError("unrecovered tile jump table")
                # Two evaluator switches, with four entries each in this fixed
                # original. Require each target to decode as executable.
                table=op.mem.disp
                for index in range(4):
                    target=struct.unpack("<I",original.read(table+index*4,4))[0]
                    if not executable(target):raise ValueError("invalid fixed tile switch target")
                    pending.append(target)
        elif row.mnemonic.startswith("j"):
            pending.append(row.operands[0].imm);pending.append(at+row.size)
        else:pending.append(at+row.size)
    # Constant slices include original static tables used by indirect indexing.
    # Global working memory is initialized only by named bindings/state.
    constant_words={}
    for address in constants:
        if 0x4A0000<=address<0x4D0000:
            for at in range(address&~3,min((address&~3)+2048,0x4D0000),4):
                try:value=struct.unpack("<I",original.read(at,4))[0]
                except Exception:continue
                if value:constant_words[at]=value
        elif 0x45D000<=address<0x467C50:
            for at in range(address,address+16,4):
                constant_words[at]=struct.unpack("<I",original.read(at,4))[0]
    source=ROOT/"archive/2026-11/20261110_120139_810374_tile_decompilation/20261110_tiles_decompiled.c"
    text=source.read_text(encoding="utf8").split("// 0045c8e0")[0]
    bindings=[]
    chunks=re.split(r'local_14\[0\] = FUN_00478e10\("',text)[1:]
    for chunk in chunks:
        name=chunk.split('"',1)[0]
        scope_match=re.search(r"local_c = (\d+);",chunk)
        scope=int(scope_match.group(1))if scope_match else 1
        array=re.search(r'FID_conflict__memcpy\s*\(\s*&DAT_([0-9a-f]{8})',chunk)
        scalar=re.search(r'_?DAT_([0-9a-f]{8}) = \*\(undefined4 \*\)\(iVar1 \+ 0xc\)',chunk)
        if not(array or scalar):raise ValueError("unrecovered named tile field: "+name)
        address=int((array or scalar).group(1),16)
        bindings.append([zlib.crc32(name.encode("cp932")),scope,address,bool(array),name])
    destination=ROOT/"player/runtime/majiro_tile_program.lua"
    lines=["-- Generated recovered tile operations; no EXE or host pointers are loaded.",
           "return{schema=1,exe_sha256="+json.dumps(original.IDENTITY)+",entries={"]
    lines += [f'[{int(key,16)}]={value},'for key,value in sorted(roots.items())]
    lines += ["},boundaries={"]+[f'[{at}]={json.dumps(name)},'for at,name in BOUNDARIES.items()]
    lines += ["},constants={"]+[f"[{at}]={value},"for at,value in sorted(constant_words.items())]
    lines += ["},bindings={"]+[literal([h,scope,at,1 if array else 0,name])+","for h,scope,at,array,name in bindings]
    lines += ["},code={"]+[f"[{at}]={literal(row)},"for at,row in sorted(program.items())]+["}}"]
    destination.write_text("\n".join(lines)+"\n",encoding="utf8")
    write_json(ROOT/"reports/tile_program_generation.json",{
        "timestamp":timestamp(),"exe_sha256":original.IDENTITY,"operations":len(program),
        "constant_words":len(constant_words),"bindings":len(bindings),"entries":roots,
        "mnemonics":dict(Counter(row[1]for row in program.values())),
        "source_sha256":hashlib.sha256(source.read_bytes()).hexdigest(),
        "output_sha256":hashlib.sha256(destination.read_bytes()).hexdigest()})
    print("tile operations generated:",len(program),"bindings",len(bindings))


if __name__=="__main__":main()
