"""Original syscall comparisons for inverse color and indexed inverse multiplication."""
import random
import struct
from original_x86 import Original
from test_original_blend import original_page
from native_pixels import Pixels
from port_common import ROOT, write_json


def run():
    rng=random.Random(171027)
    api=Pixels()
    cases=[]
    for rectangle in ((0,0,8,4),(-2,1,7,4),(3,-1,8,3),(0,0,0,4)):
        o=Original()
        data=bytes(255 if i%4==3 else v for i,v in enumerate(rng.randbytes(128)))
        page,ptr,_=original_page(o,8,4,data)
        o.syscall(0x449a00,[page,*rectangle])
        assert api("reset")==api("create 1 8 4 0")=="OK"
        for i in range(32):
            b,g,r,_=data[i*4:i*4+4]
            assert api(f"set 1 {i%8} {i//8} {r+g*256+b*65536}")=="OK"
        assert api("reverse 1 "+" ".join(map(str,rectangle)))=="OK"
        expected=o.read(ptr,128)
        for i in range(32):
            actual=tuple(map(int,api(f"pixel 1 {i%8} {i//8}").split()[1:]))
            assert actual==(*expected[i*4:i*4+3][::-1],255),(rectangle,i,actual)
        cases.append({"operation":"reverse","rectangle":rectangle})
    for same in (False,True):
        for rectangle in ((0,0,8,4,0,0),(-1,1,7,4,1,0),(0,0,6,3,1,1)):
            o=Original()
            payloads=[rng.randbytes(32),rng.randbytes(32)]
            pages=[]
            for data in payloads:
                ptr=o.memory(data)
                page=bytearray(0x3c)
                struct.pack_into("<3I",page,0,8,4,8)
                struct.pack_into("<I",page,0x10,1)
                struct.pack_into("<I",page,0x14,8)
                struct.pack_into("<I",page,0x2c,ptr)
                pages.append((o.memory(bytes(page)),ptr))
            sx,sy,w,h,dx,dy=rectangle
            target=0 if same else 1
            o.syscall(0x449b20,[pages[0][0],sx,sy,w,h,pages[target][0],dx,dy])
            assert api("reset")=="OK"
            for ident,data in enumerate(payloads,1):
                assert api(f"create {ident} 8 4 0 8")=="OK"
                for i,v in enumerate(data):
                    assert api(f"set {ident} {i%8} {i//8} {v}")=="OK"
            assert api(f"revmulcopy 1 {sx} {sy} {w} {h} {target+1} {dx} {dy}")=="OK"
            expected=o.read(pages[target][1],32)
            actual=bytes(int(api(f"get {target+1} {i%8} {i//8}").split()[1])for i in range(32))
            assert actual==expected,(same,rectangle,actual,expected)
            cases.append({"operation":"revmulcopy","same_page":same,"rectangle":rectangle})
    write_json(ROOT/"reports/remaining_pixel_call_tests.json",{
        "status":"passed","source_exe_sha256":Original.IDENTITY,"cases":cases})
    print("remaining pixel calls:",len(cases),"original/native cases passed")


if __name__=="__main__":
    run()
