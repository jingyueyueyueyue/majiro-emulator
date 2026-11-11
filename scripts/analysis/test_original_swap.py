"""Compare the source page-swap syscall, masks, clipping and overlap."""
import random
import struct
import unicorn
from original_x86 import Original
from native_pixels import Pixels
from test_original_blend import original_page
from lua_bridge import create_lua
from port_common import ROOT, write_json, sha256,native_pixels_library


def run():
    native = Pixels()
    cases = []
    rng = random.Random(171027)
    for same, rectangle in (
        (False, (1, 1, 4, 2, 2, 0)),
        (False, (-1, 0, 5, 3, 1, 1)),
        (True, (0, 1, 4, 1, 2, 1)),
        (True, (0, 0, 4, 3, 0, 1)),
        (True, (0, 1, 4, 3, 0, 0)),
    ):
        for masked in (0, 1, 2, 3):
            o = Original()
            data = [bytes(255 if i % 4 == 3 else b for i, b in enumerate(rng.randbytes(8 * 4 * 4)))
                    for _ in range(2)]
            masks = [rng.randbytes(32) if masked & (1 << i) else None for i in range(2)]
            pages = [original_page(o, 8, 4, d, m) for d, m in zip(data, masks)]
            for page, _, mask in pages:
                if mask is not None:
                    owner = struct.unpack("<I", o.read(page + 0x20, 4))[0]
                    # This field selects the original indexed-byte routine.
                    o.machine.mem_write(owner + 0x10, struct.pack("<I", 1))
            def boundary(uc, at, size, extra):
                esp = uc.reg_read(o.reg.UC_X86_REG_ESP)
                target = struct.unpack("<I", o.read(esp, 4))[0]
                if at == 0x48d8b1:
                    size = struct.unpack("<I", o.read(esp + 4, 4))[0]
                    uc.reg_write(o.reg.UC_X86_REG_EAX, o.memory(bytes(size)))
                elif at == 0x48aa80:
                    dest, source, size = struct.unpack("<3I", o.read(esp + 4, 12))
                    uc.mem_write(dest, o.read(source, size))
                uc.reg_write(o.reg.UC_X86_REG_ESP, esp + 4)
                uc.reg_write(o.reg.UC_X86_REG_EIP, target)
            for address in (0x48d8b1, 0x48aa80, 0x4899cd):
                o.machine.hook_add(unicorn.UC_HOOK_CODE, boundary, begin=address, end=address)
            sx, sy, w, h, dx, dy = rectangle
            target = 0 if same else 1
            o.syscall(0x44a120, [pages[0][0], sx, sy, w, h, pages[target][0], dx, dy])
            assert native("reset") == "OK"
            for i in range(2):
                assert native(f"create {i+1} 8 4 0") == "OK"
                if masks[i] is not None:
                    assert native(f"alpha {i+1} {i+3} 0") == "OK"
                for at in range(32):
                    b, g, r, _ = data[i][at * 4:at * 4 + 4]
                    assert native(f"set {i+1} {at%8} {at//8} {r+g*256+b*65536}") == "OK"
                    if masks[i] is not None:
                        assert native(f"set {i+3} {at%8} {at//8} {masks[i][at]}") == "OK"
            command = f"swap 1 {sx} {sy} {w} {h} {target+1} {dx} {dy}"
            assert native(command) == "OK"
            for i, (_, pixel_pointer, mask_pointer) in enumerate(pages):
                expected = o.read(pixel_pointer, 128)
                expected_mask = o.read(mask_pointer, 32) if mask_pointer else bytes(32)
                for at in range(32):
                    actual = tuple(map(int, native(f"pixel {i+1} {at%8} {at//8}").split()[1:]))
                    want = (*expected[at*4:at*4+3][::-1], 255-expected_mask[at])
                    assert actual == want, (same, rectangle, masked, i, at, actual, want)
            cases.append({"same_page": same, "rectangle": rectangle, "mask_pair": masked})
    # A swap writes both pages; replay pruning must retain its other operand.
    lua = create_lua()
    compact = lua.execute((ROOT / "runtime/majiro_graphics_checkpoint.lua").read_text(encoding="utf-8"))["compact"]
    commands = ["create 1 2 1 0", "fill 1 0 0 2 1 255", "create 2 2 1 0",
                "fill 2 0 0 2 1 65280", "swap 1 0 0 2 1 2 0 0", "release 1"]
    pruned = list(compact(lua.table_from(commands), lua.table_from(
        {2: {"width": 2, "height": 1, "depth": 32}}, recursive=True)).values())
    assert "swap 1 0 0 2 1 2 0 0" in pruned and "create 1 2 1 0" in pruned
    write_json(ROOT / "reports/original_swap_tests.json", {
        "status": "passed", "source_exe_sha256": Original.IDENTITY,
        "dll_sha256": sha256(native_pixels_library()), "cases": cases,
        "scope": "actual source swap syscall and clipping; allocator/memcpy boundaries stubbed",
        "checkpoint_retains_both_operands": True})
    print("original/native swap:", len(cases), "passed")


if __name__ == "__main__":
    run()
