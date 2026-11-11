"""Compare real chronicle CSV reads with bounded original file_read$ x86 code."""
from __future__ import annotations
import hashlib
import json
from pathlib import Path
import struct
import sys

import pefile
import unicorn
from unicorn import x86_const as reg
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/"convert"))
from majiro_arc import Catalogue
from port_common import ROOT,archive_dir,read_json,sha256,timestamp,write_json
from lua_bridge import create_lua


class FileReader:
    """Original parser code runs; only file IO, value transport and CRT copy/search are substituted."""
    def __init__(self,sample,raw):
        self.sample=sample;self.machine=unicorn.Uc(unicorn.UC_ARCH_X86,unicorn.UC_MODE_32)
        pe=pefile.PE(sample["path"]);self.base=pe.OPTIONAL_HEADER.ImageBase
        self.machine.mem_map(self.base,(pe.OPTIONAL_HEADER.SizeOfImage+4095)&~4095)
        self.machine.mem_write(self.base,pe.get_memory_mapped_image());pe.close()
        self.DATA,self.STACK,self.STOP=0x10000000,0x20000000,0x30000000
        self.machine.mem_map(self.DATA,0x10000);self.machine.mem_map(self.STACK,0x100000)
        self.machine.mem_map(self.STOP,0x1000)
        self.file=self.DATA+0x100;self.slot=self.DATA+0x1000
        self.machine.mem_write(self.slot,struct.pack("<4I",0,0,0,self.file))
        self.raw=raw.replace(b"\r\n",b"\n");self.cursor=0;self.outputs=[]
        self.boundaries={0x41c740:"arguments",0x44a712:"fgets",0x44a67b:"strchr",0x44a605:"strstr",
                         0x449930:"strcpy",0x44a2d0:"memmove",0x41c860:"result"}
        for address in self.boundaries:
            self.machine.hook_add(unicorn.UC_HOOK_CODE,self.hook,begin=address,end=address)
    def u32(self,at):return struct.unpack("<I",self.machine.mem_read(at,4))[0]
    def string(self,at):
        result=bytearray()
        for offset in range(65536):
            byte=self.machine.mem_read(at+offset,1)[0]
            if byte==0:return bytes(result)
            result.append(byte)
        raise ValueError("original fixture string limit")
    def hook(self,uc,at,size,user):
        kind=self.boundaries.get(at)
        if not kind:return
        sp=uc.reg_read(reg.UC_X86_REG_ESP);ret=self.u32(sp);result=0
        arg=lambda i:self.u32(sp+4*i)
        if kind=="arguments":result=self.slot
        elif kind=="fgets":
            dest,maximum=arg(1),arg(2)
            if self.cursor<len(self.raw):
                end=self.raw.find(b"\n",self.cursor)
                end=len(self.raw)if end<0 else end+1
                end=min(end,self.cursor+maximum-1)
                uc.mem_write(dest,self.raw[self.cursor:end]+b"\0");self.cursor=end;result=dest
        elif kind=="strchr":
            raw=self.string(arg(1));value=arg(2)&255
            offset=raw.find(bytes([value]));result=arg(1)+offset if offset>=0 else 0
        elif kind=="strstr":
            offset=self.string(arg(1)).find(self.string(arg(2)));result=arg(1)+offset if offset>=0 else 0
        elif kind=="strcpy":
            uc.mem_write(arg(1),self.string(arg(2))+b"\0");result=arg(1)
        elif kind=="memmove":
            uc.mem_write(arg(1),bytes(uc.mem_read(arg(2),arg(3))));result=arg(1)
        elif kind=="result":self.outputs.append(self.string(arg(1)))
        uc.reg_write(reg.UC_X86_REG_EAX,result);uc.reg_write(reg.UC_X86_REG_ESP,sp+4)
        uc.reg_write(reg.UC_X86_REG_EIP,ret)
    def read(self,count):
        for _ in range(count):
            sp=self.STACK+0x80000
            self.machine.mem_write(sp,struct.pack("<I",self.STOP))
            self.machine.reg_write(reg.UC_X86_REG_ESP,sp)
            self.machine.reg_write(reg.UC_X86_REG_EBP,0)
            self.machine.emu_start(0x4026f0,self.STOP,count=100000,timeout=1000000)
            if self.machine.reg_read(reg.UC_X86_REG_EIP)!=self.STOP:
                raise RuntimeError("original file_read$ instruction budget")
        return self.outputs


def expected_tokens(raw):
    result=[]
    for line in raw.replace(b"\r\n",b"\n").split(b"\n"):
        line=line.split(b";",1)[0].split(b"//",1)[0].strip(bytes(range(33)))
        if not line:continue
        for token in line.split(b","):
            token=token.strip(bytes(range(33)))
            if len(token)>=2 and token[:1]==token[-1:]==b'"':token=token[1:-1]
            result.append(token)
    return result


def main():
    folder=archive_dir("configuration_quote_tests")
    scan=read_json(ROOT/"reports/new_sample_scan.json")
    sample=next(row for row in scan["opcode_samples"]if row["sample"]=="reconquista")
    assert sha256(Path(sample["path"]))==sample["exe_sha256"]
    game=next(g for g in read_json(ROOT/"config/player_new_samples.json")["games"]if g["id"]=="reconquista")
    member=Catalogue(Path(game["directory"]),include_patch=False).get("ホラー年表改.csv")
    raw=member.read();lua=create_lua()
    configuration=lua.execute((ROOT/"player/runtime/player_configuration.lua").read_text(encoding="utf8"))
    fixtures=[("reported_original_csv",raw),("quoted_comma",b'"one,two",3\r\n'),
              ("unclosed_physical_lines",b'"first\nsecond",,tail\r\n'),
              ("comments_and_edge_quotes",b'"a;//ignored",b\r\nx,"keep",z //comment\r\n'),
              ("empty_and_trailing_fields",b',,"",0,\r\n\r\n//skip\r\n1,2\r\n')]
    rows=[]
    for name,data in fixtures:
        expected=expected_tokens(data)
        original=FileReader(sample,data).read(len(expected)+2)
        assert original[:len(expected)]==expected,(name,"oracle mismatch")
        assert original[-2:]==[b"",b""]
        actual,remainders=configuration["tokens"](data.decode("cp932"))
        values=[actual[i]for i in range(1,len(actual)+1)]
        assert values==[token.decode("cp932")for token in expected],(name,"Lua mismatch")
        # Read every token through the actual syscall family with fresh cached source.
        from audit_player_semantics import registered_providers
        l,_,_,system=registered_providers(overrides={"file_read":lambda filename,text=data.decode("cp932"):text})
        p=system["providers"];vm=l.table()
        handle=p["$file_open"](vm,l.table_from(["fixture.csv"]))
        reads=[p["$file_read$"](vm,l.table_from([handle]))for _ in range(len(expected)+2)]
        assert reads==[token.decode("cp932")for token in original],name
        rows.append({"name":name,"bytes":len(data),"tokens":len(expected),"original_x86_reads":len(original),
                     "sha256":hashlib.sha256(data).hexdigest(),"status":"passed"})
    # Ensure RURI's formally used configuration token stream remains identical.
    from majiro_config import config_tokens
    catalogue=Catalogue(ROOT);stable=0
    for items in catalogue.by_name.values():
        item=items[0]
        if item.extension!=".cfg":continue
        text=catalogue.patch.decode(item.read())if item.identity.startswith("translation:")else item.read().decode("cp932")
        actual,_=configuration["tokens"](text,True)
        assert[actual[i]for i in range(1,len(actual)+1)]==config_tokens(text),item.name
        stable+=1
    report={"schema":1,"timestamp":timestamp(),"status":"passed","fixtures":rows,
        "reported_source":{"game":"reconquista","script":"chronicle.mjo","offset":0x6f31,"line":1234,
            "file":member.name,"archive":member.archive.name,"sha256":hashlib.sha256(raw).hexdigest()},
        "original_exe_sha256":sample["exe_sha256"],"original_reader_va":0x4026f0,
        "rule":"Physical fgets lines and comma splitting ignore quote state; semicolon and // truncate comments; only paired edge quotes are removed per field.",
        "original_code_boundaries":{"file_getargs":"0x41c740","fgets":"0x44a712","strchr":"0x44a67b",
            "strstr":"0x44a605","strcpy":"0x449930","memmove":"0x44a2d0","string_return":"0x41c860"},
        "formal_ruri_cfg_unchanged":stable,"device_accessed":False,"windows_executable_launched":False,
        "source_files_modified":False,"evidence":str(folder.relative_to(ROOT)),
        "scope":"bounded original x86 file_read$ vs production parser/syscalls; CRT/file IO transport mocked",
        "limits":["Full native per-build fgets chunk size and EOF cursor details are outside this quote fix.",
                  "Existing fullwidth whitespace normalization for formal patch configs remains unchanged."],
        "source_sha256":{name:sha256(ROOT/name)for name in(
            "player/runtime/player_configuration.lua","scripts/analysis/test_configuration_quotes.py")}}
    write_json(ROOT/"reports/configuration_quote_tests.json",report)
    write_json(folder/"20261110_configuration_quote_tests.json",report)
    print("Configuration quotes:",sum(row["original_x86_reads"]for row in rows),"original x86 reads;",
          stable,"formal CFGs unchanged; real chronicle CSV opens and reads")
    return 0


if __name__=="__main__":raise SystemExit(main())
