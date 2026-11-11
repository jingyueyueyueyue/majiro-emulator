"""Original page ABI and independent-player checkpoint edge regressions."""
from __future__ import annotations

import json
from pathlib import Path
import struct
import sys

from lua_bridge import create_lua
from native_pixels import Pixels
from original_x86 import Original
from PIL import Image
import unicorn
from test_screen_semantics import Portable

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "convert"))
from port_common import ROOT, archive_dir, sha256, timestamp, write_json


def original_page(oracle, depth, value, mask=None):
    raw = bytes([value]) * 4 if depth == 8 else bytes((value, value+24, value+68, 255))*4
    pixels = oracle.memory(raw)
    page = bytearray(0x3c)
    struct.pack_into("<3I", page, 0, 4, 1, 4 * (depth // 8))
    struct.pack_into("<I", page, 0x10, int(depth == 8))
    struct.pack_into("<I", page, 0x14, depth)
    struct.pack_into("<I", page, 0x2c, pixels)
    if depth == 8:
        palette = bytes(component for i in range(256) for component in (i, 255-i, i//2, 0))
        struct.pack_into("<I", page, 0x28, oracle.memory(palette))
    if mask is not None:
        owner, _, _ = original_page(oracle, 8, mask)
        struct.pack_into("<I", page, 0x20, owner)
    return oracle.memory(bytes(page)), pixels, raw


def run():
    folder = archive_dir("player_graphics_edges")
    native = Pixels()
    portable = Portable(folder)
    def pixels(request):
        response=native(request)
        other=portable(request)[0]
        assert response==other, ("native/portable contract",request,response,other)
        return response
    lua = create_lua()
    compact = lua.execute((ROOT / "player/runtime/majiro_graphics_checkpoint.lua").read_text(
        encoding="utf-8"))["compact"]
    results, failures = [], []

    def check(label, function):
        try:
            details = function()
            results.append({"name": label, "status": "passed", **(details or {})})
        except Exception as error:
            failures.append({"name": label, "error": repr(error)})

    def mixed_depth(operation, source_depth, target_depth):
        original = Original()
        def diagnostic(uc, at, size, unused):
            stack = uc.reg_read(original.reg.UC_X86_REG_ESP)
            target, = struct.unpack("<I", uc.mem_read(stack, 4))
            uc.reg_write(original.reg.UC_X86_REG_EIP, target)
            uc.reg_write(original.reg.UC_X86_REG_ESP, stack+4)
        original.machine.hook_add(unicorn.UC_HOOK_CODE, diagnostic, begin=0x441150, end=0x441150)
        source, _, source_raw = original_page(original, source_depth, 23)
        target, target_pixels, initial = original_page(original, target_depth, 81)
        original.syscall(0x448e70 if operation == "copy" else 0x449050,
                         [source, 0, 0, 4, 1, target, 0, 0] + ([127] if operation == "extcopy" else []))
        expected = original.read(target_pixels, len(initial))
        assert pixels("reset") == "OK"
        indexed = Image.new("P", (4, 1), 23)
        indexed.putpalette([component for i in range(256) for component in (i//2, 255-i, i)])
        indexed_path = folder / "20261111_indexed.png"
        indexed.save(indexed_path)
        fixture=folder/"20261111_indexed.fixture"
        fixture.write_bytes(struct.pack("<III",4,1,8)+bytes(indexed.getpalette())+
                            indexed.convert("RGBA").tobytes()+indexed.tobytes())
        source_request = f"load8 1 0 {indexed_path}" if source_depth == 8 else "create 1 4 1 0"
        destination_color = initial[0] if target_depth == 8 else int.from_bytes(initial[:3][::-1], "little")
        assert native(source_request)=="OK"
        assert portable(f"load8 1 0 fixture://{fixture}" if source_depth==8 else source_request)[0]=="OK"
        for request in (f"create 2 4 1 0 {target_depth}",
                        f"fill 2 0 0 4 1 {destination_color}"):
            assert pixels(request) == "OK", request
        if source_depth == 32:
            assert pixels(f"fill 1 0 0 4 1 {int.from_bytes(source_raw[:3][::-1], 'little')}") == "OK"
        assert pixels(f"{operation} 1 0 0 4 1 2 0 0" +
                      (" 127" if operation == "extcopy" else "")) == "OK"
        after = [tuple(map(int,pixels(f"pixel 2 {x} 0").split()[1:4])) for x in range(4)]
        wanted = [tuple(expected[x*4:x*4+3][::-1]) if target_depth == 32 else (expected[x],)*3
                  for x in range(4)]
        assert wanted == after, (wanted, after)
        return {"original_exe_sha256": Original.IDENTITY,
                "expected_rgb": wanted}

    for operation in ("copy", "extcopy"):
        for source_depth, target_depth in ((8, 32), (32, 8)):
            check(f"{operation}_{source_depth}_to_{target_depth}",
                  lambda op=operation, s=source_depth, d=target_depth: mixed_depth(op, s, d))

    def replay(label, commands, live):
        def fingerprint(sequence):
            assert pixels("reset") == "OK"
            for command in sequence:
                assert pixels(command).startswith("OK"), command
            return {page: [pixels(f"pixel {page} {x} 0") for x in range(4)] for page in live}
        pruned = list(compact(lua.table_from(commands), lua.table_from(
            {page: {"width": 4, "height": 1, "depth": depth} for page, depth in live.items()},
            recursive=True)).values())
        expected, actual = fingerprint(commands), fingerprint(pruned)
        assert actual == expected, {"commands": commands, "pruned": pruned,
                                    "expected": expected, "actual": actual}
        return {"original_commands": len(commands), "compacted_commands": len(pruned)}

    fixtures = [
        ("unmasked_copy_preserves_target_mask",
         ["create 1 4 1 0", "alpha 1 2 0", "fill 2 0 0 4 1 127",
          "create 3 4 1 0", "fill 3 0 0 4 1 255", "copy 3 0 0 4 1 1 0 0", "release 3"],
         {1: 32, 2: 8}),
        ("alpha_lookup_released_source",
         ["create 1 4 1 0 8", "fill 1 0 0 4 1 13", "create 2 4 1 0 8",
          "alphatable 1 2 " + " ".join(str(255 - i) for i in range(256)), "release 1"],
         {2: 8}),
        ("mask_read_before_later_overwrite",
         ["create 1 4 1 0", "alpha 1 2 0", "fill 2 0 0 4 1 127", "fill 1 0 0 4 1 255",
          "create 3 4 1 0", "paste 3 1 0 0 4 1 0 0 1 1 0 0 0", "fill 2 0 0 4 1 255"],
         {1: 32, 2: 8, 3: 32}),
    ]
    for label, commands, live in fixtures:
        check(label, lambda label=label, commands=commands, live=live: replay(label, commands, live))
    def default_palette():
        for command in ("reset","create 1 4 1 0 8","fill 1 0 0 4 1 173",
                        "create 2 4 1 0","copy 1 0 0 4 1 2 0 0"):
            assert pixels(command)=="OK"
        assert pixels("pixel 2 0 0")=="OK 173 173 173 255"
        return {"original_allocator":"0x475030 grayscale palette loop"}
    check("new_indexed_page_grayscale_palette",default_palette)
    def png_masks_and_palette():
        for command in ("reset","create 1 4 1 0","fill 1 0 0 4 1 197121",
                        "alpha 1 2 0","fill 2 0 0 4 1 127"):
            assert pixels(command)=="OK"
        path=folder/"20261111_alpha_export.png"
        assert native(f"png 1 {path}")=="OK"
        wanted=Image.open(path).convert("RGBA").tobytes()
        actual=portable("png 1 screen")[1]
        assert actual==wanted,("associated-mask PNG",list(actual),list(wanted))
        indexed_path=folder/"20261111_indexed.png"
        fixture=folder/"20261111_indexed.fixture"
        assert native(f"load8 3 0 {indexed_path}")=="OK"
        assert portable(f"load8 3 0 fixture://{fixture}")[0]=="OK"
        assert native(f"png 3 {path}")=="OK"
        wanted=Image.open(path).convert("RGBA").tobytes()
        actual=portable("png 3 screen")[1]
        assert actual==wanted,("indexed-palette PNG",list(actual),list(wanted))
    check("PNG_export_uses_associated_mask_and_palette",png_masks_and_palette)
    def extreme_clip():
        for command in ("reset","create 1 4 1 0","create 2 4 1 0","fill 2 0 0 4 1 5329233"):
            assert pixels(command)=="OK"
        before=[pixels(f"pixel 2 {x} 0")for x in range(4)]
        for value in(-2147483648,2147483647):
            for command in (
                f"paste 2 1 {value} 0 4 1 0 0 1 1 0 0 0",
                f"paste 2 1 0 0 4 1 {value} 0 1 1 0 0 0",
                f"thumbrect 1 0 0 4 1 2 {value} 0 4 1"):
                assert pixels(command)=="OK",command
        assert before==[pixels(f"pixel 2 {x} 0")for x in range(4)]
    check("signed_coordinate_extremes",extreme_clip)
    portable.close()
    report = {"status": "failed" if failures else "passed", "timestamp": timestamp(),
              "runtime": "player/runtime/majiro_graphics_checkpoint.lua",
              "runtime_sha256": sha256(ROOT / "player/runtime/majiro_graphics_checkpoint.lua"),
              "cases": results, "failures": failures}
    write_json(folder / "report.json", report)
    write_json(ROOT / "reports/player_graphics_edge_tests.json", report)
    print(json.dumps(report, ensure_ascii=True), flush=True)
    return report


if __name__ == "__main__":
    raise SystemExit(run()["status"] != "passed")
