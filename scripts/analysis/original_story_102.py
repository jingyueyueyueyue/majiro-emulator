"""Bounded Kizoku V1.102 original writer; memory/file allocation are host boundaries."""
from collections import Counter
import struct
import unicorn

from original_opcode import OpcodeOracle
from original_story_save import LegacyStoryWriter


class EarlyStoryWriter(LegacyStoryWriter):
    HEADER = 0x4df3f0
    HEADER_SIZE = 0x9fd0
    FRAME_BLOCKS = 0x4e93c0
    WRITER = 0x417ee0

    def __init__(self, sample):
        if sample["sample"] != "kizoku":
            raise ValueError("V1.102 writer layout is pinned to the recovered Kizoku EXE")
        OpcodeOracle.__init__(self, sample)
        self.machine.mem_map(self.STOP, 4096)
        self.sample = sample
        self.heap = self.DATA + 0x70000
        self.boundaries, self.boundary_counts = Counter(), Counter()
        self.operations, self.output = [], bytearray()
        self.file_cursor, self.file_data, self.mode = 0, b"", "write"
        hooks = {
            0x42b170: self.allocate_stack, 0x4374ab: self.free,
            0x437469: self.open, 0x437282: self.close, 0x437f41: self.write,
            0x419a50: self.guard, 0x416b90: self.system_side_effect, 0x41a410: self.legacy_path,
        }
        for address, callback in hooks.items():
            self.machine.hook_add(unicorn.UC_HOOK_CODE, callback, begin=address, end=address)
        self.write_u32(0x4630a0, self.STOP + 0x100)
        self.machine.mem_write(self.STOP + 0x100, b"\xcc")
        self.machine.hook_add(unicorn.UC_HOOK_CODE, self.directory, begin=self.STOP + 0x100,
                             end=self.STOP + 0x100)

    def fixture(self, depth=0, thumbnail=False):
        if depth not in (0, 1):
            raise ValueError("V1.102 fixture supports one or two frames")
        uc = self.machine
        uc.mem_write(self.HEADER, bytes(self.HEADER_SIZE))
        uc.mem_write(self.HEADER, b"MajiroSavV1.102\0\0")
        uc.mem_write(self.HEADER + 16, b"fixture-label\0")
        uc.mem_write(self.FRAME_BLOCKS, bytes(168 * 2))
        for offset, value in ((0x90, depth), (0x9c, 2), (0xa0, 2), (0xa4, 2)):
            self.write_u32(self.HEADER + offset, value)
        self.write_u32(0x4f3bec, 0)
        pointers = [self.DATA + 0x31000 + 0x1000 * i for i in range(3)]
        for address, pointer in zip((0x4f3bdc, 0x4f3be0, 0x4f3be4), pointers):
            self.write_u32(address, pointer)
        string, array = self.DATA + 0x37000, self.DATA + 0x37100
        uc.mem_write(string, struct.pack("<3I", 64, 0, 11) + b"story-byte\0")
        uc.mem_write(array, struct.pack("<7I", 64, 0, 2, 1, 1, 0x80000000, 7) + bytes(16))
        groups = [
            [(0x11111111, 0, 1, 0xfffffffe), (0x22222222, 2, 1, string)],
            [(0x33333333, 1, 2, 0x80000000), (0x44444444, 3, 2, array)],
            [(0, 0, 3, 42), (0, 2, 3, string)],
        ]
        for pointer, values in zip(pointers, groups):
            uc.mem_write(pointer, b"".join(struct.pack("<4I", *row) for row in values))
        for index in range(depth + 1):
            frame = self.FRAME_BLOCKS + index * 168
            uc.mem_write(frame, f"LEGACY{index}.MJO".encode() + b"\0")
            uc.mem_write(frame + 128, struct.pack("<10I", 16 + index * 2, 0, 2, 0, 17, 0, 0, 0, 0, 0))
        if thumbnail:
            self.write_u32(0x4f3bec, self.DATA + 0x39000)
            uc.mem_write(self.DATA + 0x39000, bytes.fromhex("112233445566"))
            self.write_u32(self.HEADER + 0x94, 2)
            self.write_u32(self.HEADER + 0x98, 1)
        return {"depth": depth, "thumbnail": thumbnail}
