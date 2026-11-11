"""Compare actual original x86 blend routines against the new native page API."""
from __future__ import annotations
from pathlib import Path
import random
import struct
import sys

from original_x86 import Original
from native_pixels import Pixels
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "convert"))
from port_common import ROOT, sha256, write_json,player_pixels_library as native_pixels_library


def original_page(original, width, height, bgra, mask=None):
    pixels=original.memory(bgra)
    page=bytearray(0x3c)
    struct.pack_into("<3I",page,0,width,height,width*4)
    struct.pack_into("<I",page,0x14,32)
    struct.pack_into("<I",page,0x2c,pixels)
    mask_pixels=None
    if mask is not None:
        mask_pixels=original.memory(mask)
        mask_page=bytearray(0x3c)
        struct.pack_into("<3I",mask_page,0,width,height,width)
        struct.pack_into("<I",mask_page,0x14,8)
        struct.pack_into("<I",mask_page,0x2c,mask_pixels)
        struct.pack_into("<I",page,0x20,original.memory(bytes(mask_page)))
    return original.memory(bytes(page)),pixels,mask_pixels


def run():
    original = Original()
    native = Pixels()
    randomizer = random.Random(171027)
    cases = []
    for anti in (0, 1, 64, 127, 200, 254, 255):
        destination = randomizer.randbytes(4 * 8)
        source = randomizer.randbytes(4 * 8)
        # Raw page extcopy blends the fourth storage byte too. Keep it opaque
        # so the native 32-bit page fixture and raw original buffers match.
        destination = bytes(255 if i % 4 == 3 else b for i, b in enumerate(destination))
        source = bytes(255 if i % 4 == 3 else b for i, b in enumerate(source))
        dp, sp = original.memory(destination), original.memory(source)
        original.call(0x4879E0, [dp, dp, sp, 8, anti])
        expected = original.read(dp, len(destination))
        assert native("reset") == "OK"
        assert native("create 1 8 1 0") == native("create 2 8 1 0") == "OK"
        for x in range(8):
            for page, pixels in ((1, source), (2, destination)):
                r, g, b = pixels[x * 4:x * 4 + 3]
                assert native(f"fill {page} {x} 0 1 1 {r + g * 256 + b * 65536}") == "OK"
        assert native(f"extcopy 1 0 0 8 1 2 0 0 {anti}") == "OK"
        actual = bytes(int(v) for x in range(8) for v in native(f"pixel 2 {x} 0").split()[1:])
        assert actual == expected, ("original uniform transparency mismatch", anti, actual, expected)
        cases.append({"routine": "0x4879E0", "anti": anti, "pixels": 8})
    masks=bytes((0,1,63,127,200,253,254,255))
    for anti in (0,1,64,127,200,253,254,255):
        destination=bytes(255 if i%4==3 else b for i,b in enumerate(randomizer.randbytes(32)))
        source=bytes(255 if i%4==3 else b for i,b in enumerate(randomizer.randbytes(32)))
        dp,sp,mp=original.memory(destination),original.memory(source),original.memory(masks)
        original.call(0x487710,[dp,sp,8,mp,anti])
        expected=original.read(dp,32)
        assert native("reset")==native("create 1 8 1 0")==native("create 2 8 1 0")=="OK"
        assert native("alpha 1 3 0")=="OK"
        for x in range(8):
            for page,data in((1,source),(2,destination)):
                r,g,b=data[x*4:x*4+3]
                assert native(f"fill {page} {x} 0 1 1 {r+g*256+b*65536}")=="OK"
            assert native(f"set 3 {x} 0 {masks[x]}")=="OK"
        assert native(f"extcopy 1 0 0 8 1 2 0 0 {anti}")=="OK"
        actual=bytes(int(v)for x in range(8)for v in native(f"pixel 2 {x} 0").split()[1:])
        assert actual==expected,("original masked blend mismatch",anti,actual,expected)
        cases.append({"routine":"0x487710","anti":anti,"pixels":8,"masks":list(masks)})
    for anti in(0,127,255):
        for initial in(0,96,255):
            bgra=bytes((40,80,160,255))*8
            source,_,_=original_page(original,8,1,bgra,masks)
            dest,pixels,mask=original_page(original,8,1,bytes((70,50,30,255))*8,bytes([initial])*8)
            original.syscall(0x449050,[source,0,0,8,1,dest,0,0,anti])
            expected=original.read(pixels,32)
            expected_mask=original.read(mask,8)
            assert native("reset")==native("create 1 8 1 0")==native("create 2 8 1 0")=="OK"
            assert native("alpha 1 3 0")==native("alpha 2 4 0")=="OK"
            assert native("fill 1 0 0 8 1 2642080")==native("fill 2 0 0 8 1 4600350")=="OK"
            assert native(f"fill 4 0 0 8 1 {initial}")=="OK"
            for x,value in enumerate(masks):
                assert native(f"set 3 {x} 0 {value}")=="OK"
            assert native(f"extcopy 1 0 0 8 1 2 0 0 {anti}")=="OK"
            for x in range(8):
                r,g,b,alpha=map(int,native(f"pixel 2 {x} 0").split()[1:])
                assert(r,g,b)==tuple(expected[x*4:x*4+3][::-1]),(anti,initial,x)
                assert alpha==255-expected_mask[x],(anti,initial,x,alpha,expected_mask[x])
            cases.append({"routine":"0x449050","anti":anti,"destination_mask":initial,"pixels":8})
    for extended in(False,True):
        source,_,_=original_page(original,8,1,bytes((40,80,160,255))*8)
        dest,_,mask=original_page(original,8,1,bytes((70,50,30,255))*8,masks)
        original.syscall(0x449050 if extended else 0x448e70,
                         [source,0,0,8,1,dest,0,0]+([0]if extended else []))
        assert original.read(mask,8)==masks
        assert native("reset")==native("create 1 8 1 0")==native("create 2 8 1 0")=="OK"
        assert native("alpha 2 3 0")=="OK"
        for x,value in enumerate(masks):assert native(f"set 3 {x} 0 {value}")=="OK"
        assert native(("extcopy"if extended else"copy")+" 1 0 0 8 1 2 0 0"+(" 0"if extended else""))=="OK"
        assert[255-int(native(f"pixel 2 {x} 0").split()[-1])for x in range(8)]==list(masks)
        cases.append({"routine":"0x449050"if extended else"0x448E70",
                      "source_mask":False,"destination_mask_preserved":True,"pixels":8})
    report = {"status": "passed", "cases": cases, "pixels": 8 * len(cases),
              "source_exe_sha256": Original.IDENTITY,
              "dll_sha256": sha256(native_pixels_library()),
              "scope": "original row blends and full extcopy syscall including independent destination masks"}
    write_json(ROOT / "reports/original_blend_tests.json", report)
    print("original/native blend: passed", report["pixels"], "pixels")
    return report


if __name__ == "__main__":
    run()
