"""Compare original draw-mode dispatch and actual x86 pixels with native sprites."""
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
    original=Original()
    native=Pixels()
    randomizer=random.Random(171027)
    cases=[]
    for mode in (1,3,4):
        for anti in (0,1,64,127,200,254,255):
            source=bytes(255 if i%4==3 else x for i,x in enumerate(randomizer.randbytes(32)))
            destination=bytes(255 if i%4==3 else x for i,x in enumerate(randomizer.randbytes(32)))
            sp,dp=original.memory(source),original.memory(destination)
            def page(pointer):
                value=bytearray(0x40)
                struct.pack_into("<3I",value,0,8,1,32)
                struct.pack_into("<I",value,0x1c,mode)
                struct.pack_into("<I",value,0x2c,pointer)
                return original.memory(bytes(value))
            source_page,destination_page=page(sp),page(dp)
            rect=original.memory(struct.pack("<4I",0,0,8,1))
            point=original.memory(struct.pack("<2I",0,0))
            # 0x470760 dispatches modes 1,3,4 to 47C930/47CF00/47CD90.
            original.call(0x470760,[source_page,rect,destination_page,point,anti])
            expected=original.read(dp,32)
            assert native("reset")==native("create 1 8 1 0")==native("create 2 8 1 0")=="OK"
            for x in range(8):
                for handle,data in((1,source),(2,destination)):
                    r,g,b=data[x*4:x*4+3]
                    assert native(f"fill {handle} {x} 0 1 1 {r+g*256+b*65536}")=="OK"
            assert native(f"drawmode 1 {mode}")=="OK"
            assert native(f"paste 2 1 0 0 8 1 0 0 1 1 0 {anti} 0")=="OK"
            actual=bytes(int(v)for x in range(8)for v in native(f"pixel 2 {x} 0").split()[1:])
            assert actual==expected,(mode,anti,actual,expected)
            cases.append({"mode":mode,"anti":anti,"pixels":8})
    report={"status":"passed","cases":cases,"pixels":8*len(cases),
            "source_exe_sha256":Original.IDENTITY,
            "dll_sha256":sha256(native_pixels_library()),
            "scope":"original unit-scale sprite draw-mode dispatcher; transformed path not independently compared"}
    write_json(ROOT/"reports/drawmode_tests.json",report)
    print("original draw modes:",len(cases),"cases passed")
    return report


if __name__=="__main__":
    run()
