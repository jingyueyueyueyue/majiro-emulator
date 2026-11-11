"""Bounded original V1.105 story capture/writer/reader with memory-only I/O.

The source state is a documented fixture, not a real gameplay save. Original
frame relocation, value copying, pool serialization and file assembly execute
unchanged; media, directory creation and post-load resource refresh are explicit
host boundaries. No guest Windows executable is launched.
"""
from __future__ import annotations

from collections import Counter
from pathlib import Path
import hashlib
import struct

import unicorn
from unicorn import x86_const as reg

from test_native_saves import NativeSaveOracle
from original_opcode import OpcodeOracle


class StorySaveOracle(NativeSaveOracle):
    HEADER = 0x557460
    HEADER_SIZE = 0xA560
    FRAME_BLOCKS = 0x561DE0
    FRAME_STRIDE = 0xA8
    CAPTURE = 0x450B70
    WRITER = 0x4516B0
    READER = 0x451C30

    def __init__(self, sample):
        if sample["sample"] != "narci_onetwo":
            raise ValueError("story-save addresses currently validated for onetwo only")
        super().__init__(sample)
        self.sample = sample
        self.operations = []
        self.output = bytearray()
        self.mode = "write"
        self.boundary_counts = Counter()
        self.source_scripts = {}
        self.last_error = None
        hooks = {
            0x49528D: self.write,
            0x4573D0: self.guard,
            0x451450: self.system_side_effect,
            0x45B2F0: self.resource_refresh,
            0x43A4E0: self.resolve_script,
        }
        for address, callback in hooks.items():
            self.machine.hook_add(unicorn.UC_HOOK_CODE, callback, begin=address, end=address)
        self.write_u32(0x4B41EC, self.STOP + 0x100)
        self.machine.mem_write(self.STOP + 0x100, b"\xcc")
        self.machine.hook_add(unicorn.UC_HOOK_CODE, self.directory, begin=self.STOP+0x100, end=self.STOP+0x100)

    def open(self, uc, at, size, unused):
        self.boundary_counts["memory_fopen"] += 1
        self.file_cursor = 0
        if self.mode == "write":
            self.output = bytearray()
        self.ret(0x1234)

    def path(self, uc, at, size, unused):
        self.boundary_counts["fixture_path_resolution_fastcall"] += 1
        self.ret(uc.reg_read(reg.UC_X86_REG_ECX))

    def close(self, uc, at, size, unused):
        self.boundary_counts["memory_fclose"] += 1
        self.ret(0)

    def write(self, uc, at, size, unused):
        pointer, length, count, handle = [self.argument(i) for i in range(4)]
        if handle != 0x1234 or count != 1 or length > 0x40000:
            raise ValueError("unexpected original story fwrite")
        raw = bytes(uc.mem_read(pointer, length)) if length else b""
        self.operations.append({"action": "write", "source_va": f"{pointer:08x}",
                                "file_offset": len(self.output), "bytes": length,
                                "sha256": hashlib.sha256(raw).hexdigest()})
        self.output.extend(raw)
        self.boundary_counts["memory_fwrite"] += 1
        self.ret(int(bool(length)))

    def read(self, uc, at, size, unused):
        start = self.file_cursor
        pointer, length, count, handle = [self.argument(i) for i in range(4)]
        super().read(uc, at, size, unused)
        self.operations.append({"action": "read", "target_va": f"{pointer:08x}",
                                "file_offset": start, "bytes": length,
                                "actual_bytes": self.file_cursor - start})

    def guard(self, uc, at, size, unused):
        self.boundary_counts["fixture_save_write_guard"] += 1
        self.ret(0)

    def system_side_effect(self, uc, at, size, unused):
        self.boundary_counts["excluded_automatic_system_save"] += 1
        self.ret(0)

    def directory(self, uc, at, size, unused):
        self.boundary_counts["excluded_CreateDirectoryA"] += 1
        esp = uc.reg_read(reg.UC_X86_REG_ESP)
        target = self.read_u32(esp)
        uc.reg_write(reg.UC_X86_REG_EAX, 1)
        uc.reg_write(reg.UC_X86_REG_ESP, esp + 12)
        uc.reg_write(reg.UC_X86_REG_EIP, target)

    def resource_refresh(self, uc, at, size, unused):
        self.boundary_counts["excluded_postload_resource_refresh"] += 1
        self.ret(0)

    def resolve_script(self, uc, at, size, unused):
        pointer = uc.reg_read(reg.UC_X86_REG_ECX)
        name = bytes(uc.mem_read(pointer, 128)).split(b"\0", 1)[0]
        if name not in self.source_scripts:
            raise ValueError("story load requested an unknown script")
        self.boundary_counts["fixture_script_registry_resolve"] += 1
        self.ret(self.source_scripts[name])

    def fixture(self, depth=0, thumbnail=False):
        if depth not in (0, 1):
            raise ValueError("story fixture supports one or two frames")
        uc = self.machine
        self.heap = self.DATA + 0x70000
        uc.mem_write(self.HEADER, bytes(self.HEADER_SIZE))
        uc.mem_write(self.HEADER + 0x10, b"fixture-label\0")
        uc.mem_write(self.FRAME_BLOCKS, bytes(self.FRAME_STRIDE * 2))
        context = self.DATA + 0x30000
        global_values = self.DATA + 0x31000
        local_values = self.DATA + 0x32000
        stack_values = self.DATA + 0x33000
        snapshot_globals = self.DATA + 0x34000
        snapshot_locals = self.DATA + 0x35000
        snapshot_stack = self.DATA + 0x36000
        self.context = context
        self.fixture_depth = depth
        uc.mem_write(context, bytes(0x1000))
        for address in (global_values, local_values, stack_values, snapshot_globals,
                        snapshot_locals, snapshot_stack):
            uc.mem_write(address, bytes(0x1000))
        for address, value in ((0x5379E0, context), (0x58C464, context),
                               (0x58C460, context + 0x28 + depth * 0x50),
                               (0x537A18, global_values), (0x561BDC, 2),
                               (0x5379E4, snapshot_globals), (0x537A14, snapshot_locals),
                               (0x5379F0, snapshot_stack), (0x54D81C, 1024),
                               (0x54D818, 1024), (0x561BD4, 1024),
                               (0x561BD8, 1024), (0x4E6848, 0x12345678)):
            self.write_u32(address, value)
        for offset, value in ((0, depth), (4, 0), (8, 2), (0x20, local_values),
                              (0x24, stack_values)):
            self.write_u32(context + offset, value)
        string = self.DATA + 0x37000
        uc.mem_write(string, struct.pack("<3I", 64, 0, 11) + b"story-byte\0")
        array = self.DATA + 0x37100
        uc.mem_write(array, struct.pack("<7I", 64, 0, 2, 1, 1, 0x80000000, 7) + bytes(16))
        for address, rows in (
            (global_values, [(0x11111111, 0, 1, 0xFFFFFFFE), (0x22222222, 2, 1, string)]),
            (local_values, [(0x33333333, 1, 2, 0x80000000), (0x44444444, 3, 2, array)]),
            (stack_values, [(0, 0, 3, 42), (0, 2, 3, string)]),
        ):
            uc.mem_write(address, b"".join(struct.pack("<4I", *row) for row in rows))
        scripts = []
        self.source_scripts = {}
        for index in range(depth + 1):
            name = f"FIXTURE{index}.MJO".encode("ascii")
            address = self.DATA + 0x40000 + index * 0x200
            code = self.DATA + 0x50000 + index * 0x1000
            record = bytearray(0xB0)
            record[:len(name)+1] = name + b"\0"
            struct.pack_into("<I", record, 0x90, 0x100)
            struct.pack_into("<I", record, 0xA4, code)
            struct.pack_into("<I", record, 0xA8, address + 0x200 if index < depth else 0)
            uc.mem_write(address, bytes(record))
            uc.mem_write(code, b"\x91\x01" * 128)
            self.source_scripts[name] = address
            frame = context + 0x28 + index * 0x50
            fields = {0: code + 0x10 + index * 2, 4: 0xABC0 + index, 8: 2,
                      0xC: 0xDEF0 + index, 0x10: 17 + index, 0x14: code + 0x20,
                      0x18: code + 0x30, 0x1C: code + 0x40, 0x20: code + 0x50,
                      0x24: code + 0x60}
            for offset, value in fields.items():
                self.write_u32(frame + offset, value)
            scripts.append({"name": name.decode("ascii"), "code_base": code,
                            "frame_fields": {f"{k:02x}": v for k, v in fields.items()}})
        self.write_u32(0x4E6824, self.DATA + 0x40000)
        uc.mem_write(0x54D820, bytes(40000))
        self.write_u32(0x54D820, 0x31415926)
        self.write_u32(0x54D824, 0x27182818)
        if thumbnail:
            pixels = self.DATA + 0x39000
            uc.mem_write(pixels, bytes.fromhex("1122334455667788"))
            self.write_u32(0x537A0C, pixels)
            self.write_u32(self.HEADER + 0x214, 2)
            self.write_u32(self.HEADER + 0x218, 1)
        else:
            self.write_u32(0x537A0C, 0)
        self.expected = {"depth": depth, "scripts": scripts, "thumbnail": thumbnail,
                         "global_values": [0xFFFFFFFE, b"story-byte".hex()],
                         "local_float_bits": 0x80000000,
                         "local_array": [0x80000000, 7], "stack_values": [42, b"story-byte".hex()]}
        return self.expected

    def capture_and_write(self):
        self.call(self.CAPTURE)
        self.operations.clear()
        self.mode = "write"
        self.call(self.WRITER, self.DATA + 0x60000)
        return bytes(self.output)

    def load(self, data):
        self.mode = "read"
        self.file_data = data
        self.operations.clear()
        # Establish destination context without reusing source state values.
        self.machine.mem_write(self.context + 0x28, bytes(0xA0))
        self.machine.mem_write(self.DATA + 0x31000, bytes(0x1000))
        self.call(self.READER)
        return self.destination()

    def destination(self):
        uc = self.machine
        context = self.read_u32(0x58C464)
        frames = []
        for index in range(self.read_u32(context) + 1):
            frame = context + 0x28 + index * 0x50
            frames.append({f"{offset:02x}": self.read_u32(frame + offset)
                           for offset in (0, 4, 8, 0xC, 0x10, 0x14, 0x18, 0x1C, 0x20, 0x24)})

        def values(pointer, count):
            out = []
            for index in range(count):
                hash_, tag, scope, payload = struct.unpack("<4I", uc.mem_read(pointer + index * 16, 16))
                if tag == 2:
                    result = bytes(uc.mem_read(payload + 12, self.read_u32(payload + 8) - 1)).hex()
                elif tag == 3:
                    dims = [self.read_u32(payload + offset) for offset in (8, 12, 16)]
                    result = [self.read_u32(payload + 20 + i * 4)
                              for i in range(dims[0] * dims[1] * dims[2])]
                else:
                    result = payload
                out.append({"hash": hash_, "tag": tag, "scope": scope, "value": result})
            return out
        return {"frames": frames,
                "global_values": values(self.read_u32(0x537A18), 2),
                "local_values": values(self.read_u32(context + 0x20), 2),
                "stack_values": values(self.read_u32(context + 0x24), 2)}


class LegacyStoryWriter(StorySaveOracle):
    """The himeko V1.103 writer receives an explicit pre-captured fixture.

    This intentionally makes a weaker claim than the onetwo capture/restore
    oracle: frame blocks are authored fixture inputs, the original writer
    serializes values/pool and assembles the slot, and no V1.103 reader restore
    is asserted.
    """
    HEADER = 0x52C8A0
    HEADER_SIZE = 0xA3E0
    FRAME_BLOCKS = 0x536C80
    WRITER = 0x4282A0

    def __init__(self, sample):
        if sample["sample"] != "narci_himeko":
            raise ValueError("legacy story-save addresses validated for himeko only")
        OpcodeOracle.__init__(self, sample)
        self.machine.mem_map(self.STOP, 4096)
        self.sample = sample
        self.heap = self.DATA + 0x70000
        self.boundaries, self.boundary_counts = Counter(), Counter()
        self.operations, self.output = [], bytearray()
        self.file_cursor, self.file_data, self.mode = 0, b"", "write"
        hooks = {
            0x451A40: self.allocate_stack, 0x46221B: self.free,
            0x4621D9: self.open, 0x461FF2: self.close,
            0x462F63: self.write, 0x42C530: self.guard,
            0x427150: self.system_side_effect, 0x42E270: self.legacy_path,
        }
        for address, callback in hooks.items():
            self.machine.hook_add(unicorn.UC_HOOK_CODE, callback, begin=address, end=address)
        self.write_u32(0x49E140, self.STOP + 0x100)
        self.machine.mem_write(self.STOP + 0x100, b"\xcc")
        self.machine.hook_add(unicorn.UC_HOOK_CODE, self.directory, begin=self.STOP+0x100, end=self.STOP+0x100)

    def legacy_path(self, uc, at, size, unused):
        self.boundary_counts["fixture_path_resolution_cdecl"] += 1
        self.ret(self.argument(0))

    def fixture(self, depth=0, thumbnail=False):
        if depth not in (0, 1):
            raise ValueError("legacy story fixture supports one or two frames")
        uc = self.machine
        uc.mem_write(self.HEADER, bytes(self.HEADER_SIZE))
        uc.mem_write(self.HEADER, b"MajiroSavV1.103\0\0")
        uc.mem_write(self.HEADER + 0x10, b"fixture-label\0")
        uc.mem_write(self.FRAME_BLOCKS, bytes(0xA8 * 2))
        for offset, value in ((0x90, depth), (0x9C, 2), (0xA0, 2), (0xA4, 2)):
            self.write_u32(self.HEADER + offset, value)
        self.write_u32(0x54152C, 0)
        pointers = [self.DATA + 0x31000 + 0x1000 * i for i in range(3)]
        for global_address, pointer in zip((0x54151C, 0x541520, 0x541524), pointers):
            self.write_u32(global_address, pointer)
        string = self.DATA + 0x37000
        uc.mem_write(string, struct.pack("<3I", 64, 0, 11) + b"story-byte\0")
        array = self.DATA + 0x37100
        uc.mem_write(array, struct.pack("<7I", 64, 0, 2, 1, 1, 0x80000000, 7) + bytes(16))
        groups = [
            [(0x11111111, 0, 1, 0xFFFFFFFE), (0x22222222, 2, 1, string)],
            [(0x33333333, 1, 2, 0x80000000), (0x44444444, 3, 2, array)],
            [(0, 0, 3, 42), (0, 2, 3, string)],
        ]
        for pointer, rows in zip(pointers, groups):
            uc.mem_write(pointer, b"".join(struct.pack("<4I", *row) for row in rows))
        for index in range(depth + 1):
            at = self.FRAME_BLOCKS + index * 0xA8
            uc.mem_write(at, f"LEGACY{index}.MJO".encode("ascii") + b"\0")
            for offset, value in ((0x80, 0x10 + index * 2), (0x84, 0xABC0 + index),
                                  (0x88, 2), (0x8C, 0xDEF0 + index), (0x90, 17 + index),
                                  (0x94, 0x20), (0x98, 0x30), (0x9C, 0x40),
                                  (0xA0, 0x50), (0xA4, 0x60)):
                self.write_u32(at + offset, value)
        if thumbnail:
            pixels = self.DATA + 0x39000
            uc.mem_write(pixels, bytes.fromhex("112233445566"))
            self.write_u32(0x54152C, pixels)
            self.write_u32(self.HEADER + 0x94, 2)
            self.write_u32(self.HEADER + 0x98, 1)
        self.expected = {"depth": depth, "thumbnail": thumbnail, "fixture": "pre-captured native values/frames",
                         "original_capture_executed": False, "original_reader_executed": False}
        return self.expected

    def capture_and_write(self):
        self.operations.clear()
        self.mode = "write"
        self.call(self.WRITER, args=(self.DATA + 0x60000,))
        return bytes(self.output)


def inspect_story_layout(data):
    """Bounded structural decoder for writer evidence; no VM import."""
    import sys
    sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "convert"))
    from majiro_native_save import _Pool

    profiles = {b"MajiroSavV1.103\0": (0xA3E0, 0x90, 3),
                b"MajiroSavV1.105\0": (0xA560, 0x210, 4)}
    if len(data) > 64 * 1024 * 1024 or data[:16] not in profiles:
        raise ValueError("unknown or oversized original story-slot data")
    header_size, fields, pixel_bytes = profiles[data[:16]]
    if len(data) < header_size:
        raise ValueError("truncated story header")
    depth, width, height, globals_, locals_, stack, pool_size = struct.unpack_from("<7I", data, fields)
    if depth > 1000 or max(globals_, locals_, stack) > 1000000:
        raise ValueError("story frame/value count exceeds research bound")
    thumbnail_bytes = width * height * pixel_bytes
    frame_bytes = (depth + 1) * 0xA8
    value_bytes = (globals_ + locals_ + stack) * 16
    expected = header_size + thumbnail_bytes + frame_bytes + value_bytes + pool_size
    if expected != len(data):
        raise ValueError("story length fields disagree with file or trailing data")
    cursor = header_size
    sections = [{"name": "header", "offset": 0, "bytes": header_size},
                {"name": "thumbnail", "offset": cursor, "bytes": thumbnail_bytes,
                 "width": width, "height": height, "pixel_bytes": pixel_bytes}]
    cursor += thumbnail_bytes
    frames = []
    sections.append({"name": "frames", "offset": cursor, "bytes": frame_bytes})
    for index in range(depth + 1):
        raw = data[cursor + index*0xA8:cursor + (index+1)*0xA8]
        if b"\0" not in raw[:128] or not raw[:128].split(b"\0", 1)[0]:
            raise ValueError("invalid story frame script name")
        frames.append({"script_bytes_hex": raw[:128].split(b"\0", 1)[0].hex(),
                       "fields": {f"{at:02x}": struct.unpack_from("<I", raw, at)[0]
                                  for at in range(0x80, 0xA8, 4)}})
    cursor += frame_bytes
    pool_start = cursor + value_bytes
    pool = _Pool(data[pool_start:])
    groups = {}
    for label, count in (("globals", globals_), ("locals", locals_), ("stack", stack)):
        sections.append({"name": label, "offset": cursor, "bytes": count * 16})
        values = []
        for index in range(count):
            hash_, tag, offset, bits = struct.unpack_from("<4I", data, cursor + index*16)
            if tag > 5:
                raise ValueError("unknown story value type")
            value = pool.string(offset) if tag == 2 else pool.array(offset, tag) if tag >= 3 else bits
            values.append({"hash": f"{hash_:08x}", "native_type": tag, "value": value})
        groups[label] = values
        cursor += count * 16
    sections.append({"name": "object_pool", "offset": pool_start, "bytes": pool_size})
    return {"signature": data[:16].rstrip(b"\0").decode("ascii"), "header_size": header_size,
            "count_fields_offset": fields, "frame_depth": depth, "frame_stride": 0xA8,
            "frame_count": depth+1, "frames": frames, "groups": groups, "sections": sections,
            "label_bytes_hex": data[16:fields].split(b"\0", 1)[0].hex(),
            "opaque_header_range": {"offset": fields+28, "bytes": header_size-40000-fields-28},
            "picture_hash_offset": header_size-40000, "vm_restore_supported": False}
