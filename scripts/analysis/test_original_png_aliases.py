"""Original PNG underscore resolver and pixel conversion, memory-only host fixtures."""
from __future__ import annotations
import ctypes
import hashlib
import json
from pathlib import Path
import struct
import sys
import unicorn
from unicorn import x86_const as reg
from original_strings import StringOracle
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/"convert"))
from majiro_arc import Archive
from port_common import ROOT,archive_dir,timestamp,write_json


class PngOracle(StringOracle):
    def __init__(self,sample):
        super().__init__(sample)
        if sample["exe_sha256"]!="c573a0901b007c1db2cc96f6277aafe15401352b7c52f4a2bb16b6bc69ce8693":
            raise ValueError("PNG native fixture source has not been verified")
        self.files=set();self.lookups=[]
        self.host_crt=ctypes.CDLL("msvcrt")
        self.host_crt._splitpath.argtypes=[ctypes.c_char_p,*([ctypes.c_void_p]*4)]
        self.host_crt._makepath.argtypes=[ctypes.c_void_p,*([ctypes.c_char_p]*4)]
        self.host_crt._setmbcp(932)
        self.write_u32(0x53bf90,1)
        for at,callback in[(0x494895,self.split),(0x497e64,self.make),(0x482070,self.exists)]:
            self.machine.hook_add(unicorn.UC_HOOK_CODE,callback,begin=at,end=at)

    def split(self,uc,at,size,unused):
        stack=uc.reg_read(reg.UC_X86_REG_ESP)
        name,*pointers=struct.unpack("<5I",uc.mem_read(stack+4,20))
        parts=[ctypes.create_string_buffer(260)for _ in range(4)]
        self.host_crt._splitpath(self._input(name,-1).value,*parts)
        for pointer,data in zip(pointers,parts):
            if pointer:uc.mem_write(pointer,data.value+b"\0")
        self._return(0)

    def make(self,uc,at,size,unused):
        output,*pointers=struct.unpack("<5I",uc.mem_read(uc.reg_read(reg.UC_X86_REG_ESP)+4,20))
        parts=[self._input(pointer,-1).value for pointer in pointers]
        buffer=ctypes.create_string_buffer(1024)
        self.host_crt._makepath(buffer,*parts)
        uc.mem_write(output,buffer.value+b"\0");self._return(0)

    def exists(self,uc,at,size,unused):
        name=self._input(uc.reg_read(reg.UC_X86_REG_ECX),-1).value
        self.lookups.append(name.hex());self._return(0,int(name.lower()in self.files))

    def alias(self,name,exists=False):
        raw=name.encode("cp932");source=self.DATA+0x30000;output=self.DATA+0x31000
        uc=self.machine;uc.context_restore(self.initial)
        uc.mem_write(source,raw+b"\0");uc.mem_write(output,bytes(1024))
        self.files={raw.lower()}if exists else set();self.lookups=[]
        stack=self.STACK+0xe000;uc.mem_write(stack,struct.pack("<I",self.STOP))
        uc.reg_write(reg.UC_X86_REG_ESP,stack)
        uc.reg_write(reg.UC_X86_REG_ECX,source);uc.reg_write(reg.UC_X86_REG_EDX,output)
        uc.reg_write(reg.UC_X86_REG_EFLAGS,2)
        uc.emu_start(0x48a400,self.STOP,count=100000)
        if uc.reg_read(reg.UC_X86_REG_EIP)!=self.STOP:raise ValueError("PNG alias exceeded bound")
        return {"name":name,"real_file_exists":exists,"alias":bool(uc.reg_read(reg.UC_X86_REG_EAX)),
                "resolved":self._input(output,-1).value.decode("cp932"),"lookups":list(self.lookups)}

    def pixel(self,source,bits,color=True,palette=b"",transparency=b"",key=b""):
        uc=self.machine;uc.context_restore(self.initial)
        src=self.DATA+0x32000;rgb=src+0x100;mask=src+0x200
        pal=src+0x300;alpha=src+0x700;transparent=src+0x900
        for address,data in[(src,source),(rgb,bytes(16)),(mask,b"\x7f"*16),
                             (pal,palette),(alpha,transparency),(transparent,key)]:
            uc.mem_write(address,data+bytes(16))
        stack=self.STACK+0xe000
        args=[mask,pal,bits,int(color),int(bool(transparency or key)),alpha,len(transparency),transparent]
        uc.mem_write(stack,struct.pack("<9I",self.STOP,*args))
        uc.reg_write(reg.UC_X86_REG_ESP,stack);uc.reg_write(reg.UC_X86_REG_ECX,rgb)
        uc.reg_write(reg.UC_X86_REG_EDX,src);uc.reg_write(reg.UC_X86_REG_EFLAGS,2)
        uc.emu_start(0x489e50,self.STOP,count=2000)
        if uc.reg_read(reg.UC_X86_REG_EIP)!=self.STOP:raise ValueError("PNG pixel conversion exceeded bound")
        return {"bgr":list(uc.mem_read(rgb,3)),"mask":uc.mem_read(mask,1)[0]}


def main():
    sample=next(row for row in json.loads((ROOT/"reports/opcode_dispatch.json").read_text(encoding="utf8"))["samples"]
                if row["sample"]=="narci_onetwo")
    oracle=PngOracle(sample);aliases=[]
    for name,exists,resolved,alias in[
            ("button_.png",False,"button.png",True),
            ("button_.png",True,"button_.png",False),
            ("dir/button_.png",False,"dir/button.png",True),
            ("button__last.png",False,"button__last.png",False),
            ("button__.png",False,"button_.png",True),
            ("button.png",False,"button.png",False),
            ("\u30dc\u30bf\u30f3\u8aac\u660e_.png",False,"\u30dc\u30bf\u30f3\u8aac\u660e.png",True),
    ]:
        result=oracle.alias(name,exists)
        assert result["resolved"]==resolved and result["alias"]==alias,result
        aliases.append(result)
    pixels=[]
    for alpha in(0,1,64,128,254,255):
        result=oracle.pixel(bytes((10,20,30,alpha)),32)
        assert result=={"bgr":[10,20,30],"mask":255-alpha}
        pixels.append({"case":"RGBA decoded BGR+A","alpha":alpha,**result})
        gray=oracle.pixel(bytes((80,alpha)),16,color=False)
        assert gray=={"bgr":[80,80,80],"mask":255-alpha}
        pixels.append({"case":"gray+alpha","alpha":alpha,**gray})
    palette=bytes((10,20,30,40,50,60,70,80,90))
    for index,expected in[(0,255),(1,127),(2,0)]:
        result=oracle.pixel(bytes((index,)),8,palette=palette,transparency=bytes((0,128)))
        assert result["mask"]==expected
        pixels.append({"case":"palette tRNS","index":index,**result})
    graykey=struct.pack("<5H",0,0,0,0,80)
    for gray,expected in[(80,255),(81,0)]:
        result=oracle.pixel(bytes((gray,)),8,color=False,key=graykey)
        assert result["mask"]==expected
        pixels.append({"case":"grayscale tRNS key","value":gray,**result})
    rgbkey=struct.pack("<5H",0,30,20,10,0)
    for source,expected in[(bytes((10,20,30)),255),(bytes((11,20,30)),0)]:
        result=oracle.pixel(source,24,key=rgbkey)
        assert result["mask"]==expected
        pixels.append({"case":"RGB tRNS key","source_bgr":list(source),**result})
    for source,bits,color in[(bytes((10,20,30)),24,True),(bytes((80,)),8,False)]:
        result=oracle.pixel(source,bits,color=color)
        assert result["mask"]==0x7f
        pixels.append({"case":"no alpha/tRNS does not write mask","source_bits":bits,**result})
    root=Path(sample["path"]).parent
    member=next(m for m in Archive(root/"fastdata.arc").members
                if m.name=="\u30dc\u30bf\u30f3\u8aac\u660e.png")
    png=member.read()
    width,height,depth,color=struct.unpack_from(">IIBB",png,16)
    assert (width,height,depth,color)==(150,260,8,6)
    folder=archive_dir("png_alias_tests")
    report={"schema":1,"timestamp":timestamp(),"status":"passed",
            "original_exe_sha256":sample["exe_sha256"],"alias_cases":aliases,"pixel_cases":pixels,
            "fixture_boundaries":["original CRT _splitpath/_makepath on debug host",
                                  "memory-only resource existence predicate",
                                  "pixel conversion receives already-decoded PNG row bytes; libpng not replaced at runtime"],
            "original_addresses":{"resolver":"0048a400","suffix":"0048b0f0","convert_pixel":"00489e50",
                                  "convert_rows":"00489f60","rgba_invert":"0048a0e5",
                                  "mask_allocate_8bit":"0048a347","mask_palette":"0048a373"},
            "actual_resource":{"archive":str(member.archive),"name":member.name,
                               "sha256":hashlib.sha256(png).hexdigest(),"width":width,"height":height,
                               "bit_depth":depth,"png_color_type":color},
            "device_accessed":False,"gameplay_started":False,"evidence":str(folder.relative_to(ROOT))}
    write_json(ROOT/"reports/png_alias_tests.json",report)
    write_json(folder/"20261110_png_alias_tests.json",report)
    print("Original PNG alias:",len(aliases),"filename cases;",len(pixels),"pixel alpha/tRNS cases passed")


if __name__=="__main__":main()
