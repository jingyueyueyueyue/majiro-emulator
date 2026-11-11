"""Original story audio-header capture and replay-request boundaries.

The native status lookup and copy code runs unchanged. Only critical-section
operations and the final replay transport calls are replaced. Stream handles
remain fixture-local opaque integers, never interpreted as resource IDs.
"""
from __future__ import annotations

from collections import Counter
import struct

import unicorn
from unicorn import x86_const as reg

from original_opcode import OpcodeOracle
from native_function import NativeFunctions
from original_story_save import StorySaveOracle


PROFILES = {
    "narci_onetwo": {
        "capture": 0x474DC0, "restore": 0x475000, "fastcall": True, "header": 0x557460,
        "music": (0x22C, 0x32C, 0x53BC60, 0x53BB50),
        "channels": [
            (0x42C, 0x4AC, 0x53B820, 0x53BE80, 0x53B708, 0x53B924),
            (0x52C, 0x5AC, 0x53B710, 0x53B4F0, 0x53B3D8, 0x53B818),
            (0x62C, 0x6AC, 0x53B600, 0x53BD70, 0x53B81C, 0x53B5F4),
            (0x72C, 0x7AC, 0x53BA40, 0x53B930, 0x53B814, 0x53B70C),
        ],
        "integers": [(0x82C, 0x4E53D8), (0x834, 0x4E53E4), (0x84C, 0x4E53C8),
                     (0x8B4, 0x4E53D4), (0x8B8, 0x4E53FC), (0x8BC, 0x4E53E0), (0x8C0, 0x4E53C4)],
        "stream_head": 0x53C5D8, "critical": (0x4B4230, 0x4B4234),
        "restore_helpers": [0x4752B0, 0x4765F0, 0x476C40, 0x4772F0, 0x4779A0],
        "restore_gain_slots": [0x4E53CC, 0x4E53E8, 0x4E53F8, 0x4E53DC, 0x4E53EC],
        "skip_bit30": True,
    },
    "narci_himeko": {
        "capture": 0x444CC0, "restore": 0x444F50, "fastcall": False, "header": 0x52C8A0,
        "music": (0xAC, 0x1AC, 0x59B71C, 0x59B820),
        "channels": [
            (0x2AC, 0x32C, 0x59BA28, 0x59BE38, 0x59B6FC, 0x59B6E8),
            (0x3AC, 0x42C, 0x59BB2C, 0x59BF3C, 0x59B700, 0x59B6F0),
            (0x4AC, 0x52C, 0x59BC30, 0x59C040, 0x59B704, 0x59B6F4),
            (0x5AC, 0x62C, 0x59BD34, 0x59C144, 0x59B708, 0x59B6F8),
        ],
        "integers": [(0x6AC, 0x4AC7E0), (0x6B4, 0x4AC80C), (0x6CC, 0x4AC7E8),
                     (0x734, 0x4AC7FC), (0x738, 0x4AC800), (0x73C, 0x4AC804), (0x740, 0x4AC808)],
        "stream_head": 0x5AE374, "critical": (0x49E1E4, 0x49E1E0),
        "skip_bit30": False,
    },
}


class StoryMediaOracle(OpcodeOracle):
    STOP = 0x30000000

    def __init__(self, sample):
        super().__init__(sample)
        self.profile = PROFILES[sample["sample"]]
        self.functions = NativeFunctions(self)
        self.machine.mem_map(self.STOP, 4096)
        self.counts = Counter()
        self.replay_requests = []
        for index, iat in enumerate(self.profile["critical"]):
            address = self.STOP + 0x100 + index * 16
            self.write_u32(iat, address)
            self.machine.mem_write(address, b"\xcc")
            self.machine.hook_add(unicorn.UC_HOOK_CODE, self.critical, begin=address, end=address)
        if self.profile.get("restore_helpers"):
            for channel, address in enumerate(self.profile["restore_helpers"]):
                self.machine.hook_add(unicorn.UC_HOOK_CODE, self.replay, channel, begin=address, end=address)

    def cstring(self, address):
        data = bytes(self.machine.mem_read(address, 512))
        if b"\0" not in data:
            raise ValueError("unterminated story media fixture string")
        return data.split(b"\0", 1)[0]

    def return_(self, value=0, stack_words=0):
        uc = self.machine
        esp = uc.reg_read(reg.UC_X86_REG_ESP)
        destination = self.read_u32(esp)
        uc.reg_write(reg.UC_X86_REG_EAX, value & 0xFFFFFFFF)
        uc.reg_write(reg.UC_X86_REG_EIP, destination)
        uc.reg_write(reg.UC_X86_REG_ESP, esp + 4 + stack_words * 4)

    def critical(self, uc, at, size, unused):
        self.counts["critical_section_boundary"] += 1
        self.return_(0, 1)

    def replay(self, uc, at, size, channel):
        esp = uc.reg_read(reg.UC_X86_REG_ESP)
        self.replay_requests.append({
            "channel": channel, "first_bytes_hex": self.cstring(uc.reg_read(reg.UC_X86_REG_ECX)).hex(),
            "second_bytes_hex": self.cstring(uc.reg_read(reg.UC_X86_REG_EDX)).hex(),
            "request_flags": self.read_u32(esp+4),
            "preplay_gain_bits": self.read_u32(self.profile["restore_gain_slots"][channel]),
        })
        self.counts["excluded_media_playback_transport"] += 1
        self.return_()

    def call(self, address, pointer=None):
        stack = self.STACK + 0xF000
        args = [] if self.profile["fastcall"] else [pointer or self.profile["header"]]
        self.machine.mem_write(stack, struct.pack("<"+"I"*(len(args)+1), self.STOP, *args))
        self.machine.reg_write(reg.UC_X86_REG_ESP, stack)
        self.machine.reg_write(reg.UC_X86_REG_EBP, 0)
        self.machine.reg_write(reg.UC_X86_REG_ECX, pointer or self.profile["header"])
        self.machine.emu_start(address, self.STOP, count=300000)
        if self.machine.reg_read(reg.UC_X86_REG_EIP) != self.STOP:
            raise ValueError("story media function exceeded execution bound")

    def fixture(self, music=True, channel=0, flags=1, present=True, running=1, completion=0):
        p = self.profile
        uc = self.machine
        self.replay_requests = []
        uc.mem_write(p["header"], b"\xA5" * 0x1000)
        first = b"bgm-intro.ogg" if music else b""
        second = b"bgm-loop.ogg"
        uc.mem_write(p["music"][2], first+b"\0")
        uc.mem_write(p["music"][3], second+b"\0")
        for index, row in enumerate(p["channels"]):
            self.write_u32(row[4], flags if channel == index else 0)
            self.write_u32(row[5], 0x11220000 + index)
            uc.mem_write(row[2], f"se{index+1}-intro.ogg".encode()+b"\0")
            uc.mem_write(row[3], f"se{index+1}-loop.ogg".encode()+b"\0")
        node = self.DATA + 0x50000
        uc.mem_write(node, bytes(0x700))
        self.write_u32(node+0x620, 0x11220000+channel)
        self.write_u32(node+0x650, running)
        self.write_u32(node+0x648, completion)
        self.write_u32(p["stream_head"], node if present else 0)
        for index, (_, source) in enumerate(p["integers"]):
            self.write_u32(source, 0x10203040 + index * 0x101)
        return {"music": music, "channel": channel, "flags": flags, "stream_present": present,
                "running": running, "completion": completion}

    def capture(self):
        p = self.profile
        self.call(p["capture"])
        names = [self.cstring(p["header"]+offset).hex() for offset in p["music"][:2]]
        channels = [[self.cstring(p["header"]+row[0]).hex(), self.cstring(p["header"]+row[1]).hex()]
                    for row in p["channels"]]
        integers = [self.read_u32(p["header"]+offset) for offset, _ in p["integers"]]
        return {"music": names, "channels": channels, "integers": integers}

    def restore(self):
        if not self.profile.get("restore_helpers"):
            raise ValueError("legacy replay driver has not been bounded")
        self.call(self.profile["restore"])
        return list(self.replay_requests)

    def visual_reset_markers(self):
        if not self.profile["fastcall"]:
            raise ValueError("visual reset fixture uses the onetwo build")
        uc = self.machine
        excluded = Counter()
        for address, name in ((0x479370, "sprite_transform_commit"),
                              (0x479190, "graphics_selection_refresh"),
                              (0x486080, "graphics_device_invalidation"),
                              (0x444B80, "console_internal_resource_release")):
            def skip(uc, at, size, unused, name=name):
                excluded[name] += 1
                self.return_()
            uc.hook_add(unicorn.UC_HOOK_CODE, skip, begin=address, end=address)
        sprite = self.DATA+0x60000
        uc.mem_write(sprite, b"\x5A"*0x700)
        self.write_u32(sprite+0x3AC, 0)
        self.write_u32(sprite+0x628, 0)
        self.write_u32(0x53B92C, sprite)
        self.write_u32(0x53BA34, 0)
        self.write_u32(0x53BA38, 0)
        self.write_u32(0x4E5198, 77)
        for address in (0x537998, 0x4E6848, 0x4E6840):
            self.write_u32(address, 0xABCDEF01)
        self.call(0x479690)
        self.call(0x444AC0)
        return {
            "graphics_fields": {f"{offset:03x}": self.read_u32(sprite+offset)
                                for offset in (0x3C, 0x5C, 0x27C, 0x3C8, 0x3E0, 0x500,
                                               0x34, 0x3B0, 0x18, 0x1C)},
            "console_control_fields": {f"{address:08x}": self.read_u32(address)
                                       for address in (0x537998, 0x4E6848, 0x4E6840)},
            "excluded_boundaries": dict(excluded),
            "rendered_pixels_verified": False, "console_text_restored": False,
        }

    def audio_reset_defaults(self):
        if not self.profile["fastcall"]:
            raise ValueError("audio reset fixture uses the onetwo build")
        self.fixture(True, 0, 1, True, 1, 0)
        def backend(uc, at, size, unused):
            self.counts["excluded_audio_backend_stop_all"] += 1
            self.return_()
        self.machine.hook_add(unicorn.UC_HOOK_CODE, backend, begin=0x490DB0, end=0x490DB0)
        self.write_u32(0x53B3DC, 0)
        gains = (0x4E53CC, 0x4E53F4, 0x4E53E8, 0x4E53F8,
                 0x4E53DC, 0x4E53EC, 0x4E53F0, 0x4E53D0)
        for address in gains:
            self.write_u32(address, 0x98765432)
        self.call(0x474BD0)
        return {"gain_defaults": {f"{at:08x}": self.read_u32(at) for at in gains},
                "capture_after_reset": self.capture(),
                "capture_source_gains_are_not_reset_defaults": True,
                "audio_device_stop_executed": False}


class StoryResetOracle(StorySaveOracle):
    """Execute original context/event reset, with external subsystem boundaries."""
    def __init__(self, sample):
        super().__init__(sample)
        self.reset_boundaries = Counter()
        self.freed = []
        # Memory/device/config subsystems do not affect the native context list
        # contract being tested here. Exact excluded calls remain in evidence.
        boundaries = {
            0x439800: "readmark_initialization",
            0x44F000: "input_device_initialization",
            0x47F4D0: "window_mouse_state",
            0x474BD0: "audio_backend_reset",
            0x479690: "graphics_backend_reset",
            0x444AC0: "console_backend_reset",
            0x439780: "reader_input_reset",
        }
        for address, label in boundaries.items():
            def skip(uc, at, size, unused, label=label):
                self.reset_boundaries[label] += 1
                self.ret()
            self.machine.hook_add(unicorn.UC_HOOK_CODE, skip, begin=address, end=address)
        self.write_u32(0x4B43B4, self.STOP+0x180)
        self.machine.mem_write(self.STOP+0x180, b"\xcc")
        def mouse(uc, at, size, unused):
            self.reset_boundaries["window_mouse_import"] += 1
            esp = uc.reg_read(reg.UC_X86_REG_ESP)
            target = self.read_u32(esp)
            uc.reg_write(reg.UC_X86_REG_ESP, esp+16)
            uc.reg_write(reg.UC_X86_REG_EIP, target)
        self.machine.hook_add(unicorn.UC_HOOK_CODE, mouse, begin=self.STOP+0x180, end=self.STOP+0x180)

    def free(self, uc, at, size, unused):
        self.freed.append(self.argument(0))
        self.ret()

    def reset_fixture(self):
        uc = self.machine
        self.heap = self.DATA+0x90000
        contexts = [self.DATA+0x10000, self.DATA+0x20000, self.DATA+0x30000]
        for index, pointer in enumerate(contexts):
            uc.mem_write(pointer, bytes(0x5078))
            stack = self.DATA+0x50000+index*0x1000
            values = self.DATA+0x54000+index*0x1000
            uc.mem_write(stack, bytes(256))
            uc.mem_write(values, bytes(256))
            for offset, value in ((4, index), (0x18, contexts[index-1] if index else 0),
                                  (0x1C, contexts[index+1] if index < 2 else 0),
                                  (0x20, values), (0x24, stack),
                                  (0x5034, 0xAABB0000+index)):
                self.write_u32(pointer+offset, value)
        self.write_u32(0x5379E0, contexts[0])
        self.write_u32(0x537A00, contexts[-1])
        self.write_u32(0x537A08, len(contexts))
        self.write_u32(0x5379EC, 0x12340000)
        self.write_u32(0x58C464, contexts[0])
        self.write_u32(0x58C460, contexts[0]+0x28)
        # Already allocated persistent/snapshot buffers avoid file I/O here.
        self.write_u32(0x5379F8, self.DATA+0x58000)
        self.write_u32(0x537A18, self.DATA+0x59000)
        self.write_u32(0x561BDC, 0)
        self.write_u32(0x5379E4, self.DATA+0x5A000)
        self.write_u32(self.HEADER+0x21C, 0)
        self.write_u32(self.HEADER+0x220, 0)
        self.write_u32(self.HEADER+0x224, 0)
        nodes = [self.DATA+0x60000+i*0x100 for i in range(3)]
        for index, pointer in enumerate(nodes):
            uc.mem_write(pointer, bytes(0x80))
            self.write_u32(pointer, index)
            self.write_u32(pointer+0x10, nodes[index-1] if index else 0)
            self.write_u32(pointer+0x14, nodes[index+1] if index < 2 else 0)
        self.write_u32(0x4E67F8, nodes[0])
        self.write_u32(0x4E67FC, nodes[-1])
        self.write_u32(0x53C848, 0)
        return {"context_addresses": contexts, "event_addresses": nodes}

    def execute_reset(self):
        expected = self.reset_fixture()
        self.call(0x4508A0)
        pointer = self.read_u32(0x5379E0)
        result = {"context_count": self.read_u32(0x537A08),
                  "head_id": self.read_u32(pointer+4),
                  "head_previous": self.read_u32(pointer+0x18),
                  "head_next": self.read_u32(pointer+0x1C),
                  "head_wait_marker": self.read_u32(pointer+0x5034),
                  "head_frame_depth": self.read_u32(pointer),
                  "event_head": self.read_u32(0x4E67F8), "event_tail": self.read_u32(0x4E67FC),
                  "all_old_contexts_freed": all(p in self.freed for p in expected["context_addresses"]),
                  "all_old_events_freed": all(p in self.freed for p in expected["event_addresses"]),
                  "excluded_boundaries": dict(self.reset_boundaries)}
        return result


class StoryStartupOracle(StorySaveOracle):
    """Run native post-reader startup through the HOT_RESET dispatch boundary."""
    def __init__(self, sample):
        super().__init__(sample)
        self.startup_media_calls = 0
        self.callback_request = None
        def media(uc, at, size, unused):
            self.startup_media_calls += 1
            self.ret()
        def callback(uc, at, size, unused):
            frame = self.read_u32(0x58C460)
            context = self.read_u32(0x58C464)
            count = uc.reg_read(reg.UC_X86_REG_EDX)
            top = self.read_u32(frame+8)
            values = []
            for index in range(top-count, top):
                slot = self.read_u32(context+0x24)+index*16
                tag, payload = self.read_u32(slot+4), self.read_u32(slot+12)
                value = bytes(uc.mem_read(payload+12, self.read_u32(payload+8)-1)).hex() if tag == 2 else payload
                values.append({"tag": tag, "value": value})
            self.callback_request = {"hash": f"{uc.reg_read(reg.UC_X86_REG_ECX):08x}",
                                     "argument_count": count, "arguments": values}
            uc.emu_stop()
        self.machine.hook_add(unicorn.UC_HOOK_CODE, media, begin=0x475000, end=0x475000)
        self.machine.hook_add(unicorn.UC_HOOK_CODE, callback, begin=0x438960, end=0x438960)

    def execute_startup(self):
        self.fixture(1, False)
        data = self.capture_and_write()
        self.load(data)
        self.write_u32(self.context+0x10, 1024)
        self.machine.reg_write(reg.UC_X86_REG_EDI, 1)
        self.machine.reg_write(reg.UC_X86_REG_ESP, self.STACK+0xF000)
        self.machine.emu_start(0x4606F7, self.STOP, count=100000)
        if self.callback_request is None:
            raise ValueError("native startup did not reach HOT_RESET boundary")
        return {"media_replay_driver_calls": self.startup_media_calls,
                "callback": self.callback_request, "callback_execution_verified": False}
