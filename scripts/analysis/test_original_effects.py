"""Run original sepia syscall arithmetic with only handle/UI boundaries replaced."""
from __future__ import annotations
from pathlib import Path
import random
import struct
import sys

from original_x86 import Original
from native_pixels import Pixels
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/"convert"))
from port_common import ROOT,write_json,sha256,player_pixels_library as native_pixels_library


def run():
    original=Original();native=Pixels();rand=random.Random(171027)
    cases=[]
    for amount in (-1,0,35,128,255):
        rgba=bytes(255 if i%4==3 else n for i,n in enumerate(rand.randbytes(32)))
        bgra=b''.join(bytes((rgba[i+2],rgba[i+1],rgba[i],rgba[i+3]))for i in range(0,32,4))
        pixels=original.memory(bgra)
        page=bytearray(0x40)
        struct.pack_into("<4I",page,0,8,1,32,0)
        struct.pack_into("<I",page,0x14,32)
        struct.pack_into("<I",page,0x2c,pixels)
        handle=original.memory(bytes(page))
        low,high=468277,13165567
        args=[handle,0,0,8,1,low,high]
        if amount>=0:args.append(amount)
        original.syscall(0x449c50,args)
        expected=original.read(pixels,32)
        expected=b''.join(bytes((expected[i+2],expected[i+1],expected[i],expected[i+3]))
                          for i in range(0,32,4))
        assert native("reset")==native("create 1 8 1 0")=="OK"
        for x in range(8):
            r,g,b=rgba[x*4:x*4+3]
            assert native(f"fill 1 {x} 0 1 1 {r+g*256+b*65536}")=="OK"
        request=f"sepia 1 0 0 8 1 {low} {high}"+(f" {amount}"if amount>=0 else "")
        assert native(request)=="OK"
        actual=bytes(int(v)for x in range(8)for v in native(f"pixel 1 {x} 0").split()[1:])
        assert actual==expected,(amount,actual,expected)
        cases.append({"amount":amount,"pixels":8})
    report={"status":"passed","cases":cases,"source_exe_sha256":Original.IDENTITY,
            "dll_sha256":sha256(native_pixels_library())}
    write_json(ROOT/"reports/original_effect_tests.json",report)
    print("original/native sepia: passed",len(cases),"cases")
    return report


if __name__=="__main__":run()
