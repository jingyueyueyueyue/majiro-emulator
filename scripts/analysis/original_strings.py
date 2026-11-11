"""Run original string code with its CRT initialized and real Windows NLS calls.

CRT lock boundaries are omitted in this single-thread fixture. Character
classification, case conversion, allocation-free comparisons and bytecode
dispatch remain the original implementation. Windows character APIs run on
the debug host; unknown imports stop instead of returning invented values.
"""
from __future__ import annotations

from collections import Counter
import ctypes
import re
import struct

import capstone
import pefile
import unicorn
from unicorn import x86_const as reg

from original_opcode import OpcodeOracle


class StringOracle(OpcodeOracle):
    IMPORTS = 0x31000000
    STOP = 0x31010000

    def __init__(self, sample, code_page=932):
        super().__init__(sample)
        self.code_page = code_page
        self.api_calls = Counter()
        self.lock_calls = Counter()
        self.kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        prototypes = {
            "GetCPInfo": [ctypes.c_uint, ctypes.c_void_p],
            "GetStringTypeA": [ctypes.c_uint, ctypes.c_uint, ctypes.c_void_p, ctypes.c_int, ctypes.c_void_p],
            "GetStringTypeW": [ctypes.c_uint, ctypes.c_void_p, ctypes.c_int, ctypes.c_void_p],
            "LCMapStringA": [ctypes.c_uint, ctypes.c_uint, ctypes.c_void_p, ctypes.c_int,
                             ctypes.c_void_p, ctypes.c_int],
            "LCMapStringW": [ctypes.c_uint, ctypes.c_uint, ctypes.c_void_p, ctypes.c_int,
                             ctypes.c_void_p, ctypes.c_int],
            "MultiByteToWideChar": [ctypes.c_uint, ctypes.c_uint, ctypes.c_void_p, ctypes.c_int,
                                    ctypes.c_void_p, ctypes.c_int],
            "WideCharToMultiByte": [ctypes.c_uint, ctypes.c_uint, ctypes.c_void_p, ctypes.c_int,
                                    ctypes.c_void_p, ctypes.c_int, ctypes.c_void_p, ctypes.c_void_p],
            "GetLocaleInfoA": [ctypes.c_uint, ctypes.c_uint, ctypes.c_void_p, ctypes.c_int],
            "GetLocaleInfoW": [ctypes.c_uint, ctypes.c_uint, ctypes.c_void_p, ctypes.c_int],
        }
        for name, args in prototypes.items():
            fn = getattr(self.kernel, name)
            fn.argtypes, fn.restype = args, ctypes.c_int
        pe = pefile.PE(sample["path"])
        # Some wrappers mark their embedded interpreter as data and grant
        # execution at initialization. Recovered dispatch handlers establish
        # which section contains code even before that permission transition.
        code_sections=[section for section in pe.sections if section.Characteristics&0x20000000 or any(
            pe.OPTIONAL_HEADER.ImageBase+section.VirtualAddress<=row["selected_va"]<
            pe.OPTIONAL_HEADER.ImageBase+section.VirtualAddress+section.Misc_VirtualSize
            for row in self.rows.values())]
        self.machine.mem_map(self.IMPORTS, 0x20000)
        # The compiler's SEH prologues link/unlink fs:[0]. No exception is
        # dispatched in these bounded character fixtures.
        self.machine.mem_map(0, 0x1000)
        self.write_u32(0, 0xFFFFFFFF)
        self.import_names = {}
        for library in pe.DIRECTORY_ENTRY_IMPORT:
            for entry in library.imports:
                at = self.IMPORTS + len(self.import_names) * 16
                if at >= self.STOP:
                    raise ValueError("original import fixture exceeds bound")
                name = entry.name.decode("ascii") if entry.name else f"ordinal_{entry.ordinal}"
                self.import_names[at] = name
                self.machine.mem_write(at, b"\xCC")
                self.write_u32(entry.address, at)
        self.machine.hook_add(unicorn.UC_HOOK_CODE, self._import,
                              begin=self.IMPORTS, end=self.STOP-1)
        self.crt_initialization = "not_needed_by_recovered_build"
        pattern = re.compile(rb"\x55\x8b\xec\x83\xec\x18\x53\x56\x57\x6a\x19")
        candidates = []
        for section in code_sections:
            candidates += [pe.OPTIONAL_HEADER.ImageBase + section.VirtualAddress + match.start()
                           for match in pattern.finditer(section.get_data())]
        if len(candidates) > 1:
            raise ValueError("ambiguous original multibyte CRT initializer")
        if candidates:
            address = candidates[0]
            md = capstone.Cs(capstone.CS_ARCH_X86, capstone.CS_MODE_32)
            rows = []
            for ins in md.disasm(bytes(self.machine.mem_read(address, 1200)), address):
                rows.append(ins)
                if ins.mnemonic == "ret":
                    break
            targets = set()
            for index, ins in enumerate(rows[:-1]):
                next_ins = rows[index+1]
                if ins.mnemonic == "push" and ins.op_str == "0x19" and next_ins.mnemonic == "call":
                    targets.add(int(next_ins.op_str, 16))
            if len(targets) != 2:
                raise ValueError("original CRT lock/unlock boundaries did not match")
            for at in targets:
                self.machine.hook_add(unicorn.UC_HOOK_CODE, self._lock, begin=at, end=at)
            stack = self.STACK + 0xF000
            self.machine.mem_write(stack, struct.pack("<II", self.STOP, code_page))
            self.machine.reg_write(reg.UC_X86_REG_ESP, stack)
            try:
                self.machine.emu_start(address, self.STOP, count=200000)
            except Exception as error:
                ip = self.machine.reg_read(reg.UC_X86_REG_EIP)
                raise RuntimeError(f"original CRT initialization at {ip:08x}: {error}; "
                                   f"APIs={dict(self.api_calls)}") from error
            if (self.machine.reg_read(reg.UC_X86_REG_EIP) != self.STOP or
                    self.machine.reg_read(reg.UC_X86_REG_EAX) != 0):
                raise ValueError("original multibyte CRT initialization failed")
            self.crt_initialization = f"original_setmbcp({code_page})"
        self.initial = self.machine.context_save()
        self.heap = self.DATA + 0x70000
        self.allocations = 0
        self.guest_allocations = {}
        md = capstone.Cs(capstone.CS_ARCH_X86, capstone.CS_MODE_32)
        start = self.rows["ldstr"]["selected_va"]
        rows = list(md.disasm(bytes(self.machine.mem_read(start, 300)), start))
        if rows[0].mnemonic == "call":
            start = int(rows[0].op_str, 16)
            rows = list(md.disasm(bytes(self.machine.mem_read(start, 300)), start))
        allocation_indices = [i for i, row in enumerate(rows) if row.mnemonic == "call"
                and rows[i-1].mnemonic == "push"
                and any(row.mnemonic == "lea" and
                           any(suffix in row.op_str for suffix in ("+ 0xc]", "+ 0x2c]"))
                           for row in rows[:i])]
        if not allocation_indices:
            raise ValueError("original string allocation boundary did not match")
        allocation_index = allocation_indices[0]
        allocation = int(rows[allocation_index].op_str, 16)
        wrapper = list(md.disasm(bytes(self.machine.mem_read(allocation, 100)), allocation))
        if any(row.mnemonic == "add" and row.op_str in ("eax, 0x10", "eax, 0x20")
               for row in wrapper[:8]):
            allocation = int(next(row.op_str for row in wrapper[:12] if row.mnemonic == "call"), 16)
        self.allocator_va = allocation
        self.machine.hook_add(unicorn.UC_HOOK_CODE, self._allocate, begin=allocation, end=allocation)
        self.errno_boundaries = set()
        for section in code_sections:
            data = section.get_data()
            for match in re.finditer(re.escape(b"\xC7\x00\x22\x00\x00\x00"), data):
                for distance in range(5, 16):
                    index = match.start() - distance
                    if index < 0 or data[index] != 0xE8:
                        continue
                    at = pe.OPTIONAL_HEADER.ImageBase + section.VirtualAddress + index
                    rows = list(md.disasm(data[index:match.end()], at))
                    if (rows and rows[0].mnemonic == "call" and rows[-1].mnemonic == "mov"
                            and rows[-1].op_str == "dword ptr [eax], 0x22"
                            and sum(row.mnemonic=="call"for row in rows)==1
                            and len(rows) <= 4):
                        self.errno_boundaries.add(int(rows[0].op_str, 16))
        for address in self.errno_boundaries:
            self.machine.hook_add(unicorn.UC_HOOK_CODE, self._errno, begin=address, end=address)
        self._initialize_modern_crt(sample)

    def _initialize_modern_crt(self,sample):
        # This VC2012 layout was recovered from original _initptd,
        # __crtGetFls/Tls wrappers and _setmbcp. Addresses are fixture evidence,
        # never runtime engine profiles or replacements for string operations.
        layouts={
            "139d1559bd83da7fa54cc8c4d30ccd45401bfba5e542c4762a5cf0e1e3b539a0":
                {"api_init":0x48ff65,"thread_init":0x48fd08,"thread_get":0x48fc81,
                 "setmbcp":0x48f4fa,"allocate":0x4930c9,
                 "locks":(0x492ba4,0x492d0e)},
        }
        layout=layouts.get(sample["exe_sha256"])
        if not layout:return
        uc=self.machine
        def run(at,args,budget=500000):
            stack=self.STACK+0xf000
            uc.mem_write(self.STACK,bytes(0x10000))
            uc.mem_write(stack,struct.pack("<"+"I"*(len(args)+1),self.STOP,*args))
            uc.reg_write(reg.UC_X86_REG_ESP,stack);uc.reg_write(reg.UC_X86_REG_EFLAGS,2)
            uc.emu_start(at,self.STOP,count=budget)
            if uc.reg_read(reg.UC_X86_REG_EIP)!=self.STOP:
                raise RuntimeError("original modern CRT initialization exceeded bound")
            return uc.reg_read(reg.UC_X86_REG_EAX)
        self.initializing_api_table=True
        try:run(layout["api_init"],[])
        finally:self.initializing_api_table=False
        thread=self.DATA+0x6a000
        uc.mem_write(thread,bytes(0x400))
        for address in layout["locks"]:
            uc.hook_add(unicorn.UC_HOOK_CODE,self._lock,begin=address,end=address)
        run(layout["thread_init"],[thread,0])
        def thread_record(machine,at,size,unused):self._return(0,thread)
        uc.hook_add(unicorn.UC_HOOK_CODE,thread_record,
                    begin=layout["thread_get"],end=layout["thread_get"])
        uc.hook_add(unicorn.UC_HOOK_CODE,self._allocate,
                    begin=layout["allocate"],end=layout["allocate"])
        if run(layout["setmbcp"],[self.code_page])!=0:
            raise ValueError("original modern CRT multibyte initialization failed")
        self.crt_initialization=f"original_encoded_api_init; original_initptd; original_setmbcp({self.code_page})"
        self.modern_crt_evidence=layout
        self.initial=uc.context_save()

    def _return(self, argument_words, result=0):
        uc = self.machine
        esp = uc.reg_read(reg.UC_X86_REG_ESP)
        address = self.read_u32(esp)
        uc.reg_write(reg.UC_X86_REG_EAX, result & 0xFFFFFFFF)
        uc.reg_write(reg.UC_X86_REG_ESP, esp + 4 + argument_words * 4)
        uc.reg_write(reg.UC_X86_REG_EIP, address)

    def _lock(self, uc, at, size, unused):
        self.lock_calls[f"{at:08x}"] += 1
        self._return(0)

    def _allocate(self, uc, at, size, unused):
        length = self.read_u32(uc.reg_read(reg.UC_X86_REG_ESP) + 4)
        if length > 128 * 1024:
            raise MemoryError("original fixture allocation exceeds bound")
        pointer = (self.heap + 15) & ~15
        self.heap = pointer + length + 32
        if self.heap >= self.DATA + 0x100000:
            raise MemoryError("original fixture heap exhausted")
        self.machine.mem_write(pointer, bytes(length + 16))
        self.guest_allocations[pointer] = length
        self.allocations += 1
        self._return(0, pointer)

    def _errno(self, uc, at, size, unused):
        # Model the single fixture thread's errno address. Original parsing
        # still sets ERANGE and selects its original saturated/wrapped result.
        self._return(0, self.DATA + 0x60000)

    def execute(self, *args, **kwargs):
        if not kwargs.pop("preserve_heap", False):
            self.heap = self.DATA + 0x70000
            self.guest_allocations = {}
        try:
            return super().execute(*args, **kwargs)
        except Exception as error:
            raise RuntimeError(f"{error}; original IP={self.machine.reg_read(reg.UC_X86_REG_EIP):08x}") from error

    def _input(self, address, count, wide=False):
        unit = 2 if wide else 1
        if count < 0:
            data = bytearray()
            for offset in range(0, 65536, unit):
                value = bytes(self.machine.mem_read(address + offset, unit))
                data.extend(value)
                if value == bytes(unit):
                    return ctypes.create_string_buffer(bytes(data))
            raise ValueError("unterminated original Windows character API input")
        if count > 32768:
            raise ValueError("original Windows character API input exceeds bound")
        return ctypes.create_string_buffer(bytes(self.machine.mem_read(address, count * unit)))

    def _import(self, uc, at, size, unused):
        name = self.import_names.get(at)
        if name is None:
            raise ValueError("unaligned original import")
        self.api_calls[name] += 1
        esp = uc.reg_read(reg.UC_X86_REG_ESP)

        def args(count):
            return list(struct.unpack("<" + "I" * count, uc.mem_read(esp + 4, count * 4)))

        def signed(value):
            return value - 0x100000000 if value >= 0x80000000 else value

        if getattr(self,"initializing_api_table",False):
            if name=="GetModuleHandleW":
                return self._return(1,1)
            if name=="GetProcAddress":
                # Exercise the original documented TLS/NLS fallbacks. A null
                # address is encoded by original code using its own cookie.
                return self._return(2,0)

        def output(address, data, length):
            if address and length:
                uc.mem_write(address, bytes(data.raw[:length]))

        if name in ("EnterCriticalSection","LeaveCriticalSection","InitializeCriticalSection","DeleteCriticalSection"):
            # This fixture owns one emulated thread. Preserve the Win32
            # stdcall boundary while recording the original lock operation.
            args(1);self.lock_calls[name]+=1
            return self._return(1)
        if name == "GetCPInfo":
            cp, address = args(2)
            data = ctypes.create_string_buffer(20)
            result = self.kernel.GetCPInfo(cp, data)
            if result:
                output(address, data, 20)
            return self._return(2, result)
        if name == "HeapFree":
            heap, flags, pointer = args(3)
            if pointer and pointer not in self.guest_allocations:
                raise ValueError(f"original free references an unknown fixture allocation: {pointer:08x}")
            self.guest_allocations.pop(pointer, None)
            return self._return(3, 1)
        if name in ("GetACP", "GetOEMCP"):
            # Explicit fixture locale; never inherit the debug PC's ACP.
            return self._return(0, self.code_page)
        if name == "GetLastError":
            return self._return(0, ctypes.get_last_error())
        if name in ("GetStringTypeA", "GetStringTypeW"):
            values = args(5 if name.endswith("A") else 4)
            if name.endswith("A"):
                locale, flags, src, count, dst = values
                source = self._input(src, signed(count))
                data = ctypes.create_string_buffer(max(len(source.raw), 1) * 2)
                result = self.kernel.GetStringTypeA(locale, flags, source, signed(count), data)
            else:
                flags, src, count, dst = values
                source = self._input(src, signed(count), True)
                data = ctypes.create_string_buffer(max(len(source.raw), 2))
                result = self.kernel.GetStringTypeW(flags, source, signed(count), data)
            if result:
                wanted = (signed(count) if signed(count) >= 0 else len(source.raw)//(
                    2 if name.endswith("W") else 1)) * 2
                output(dst, data, wanted)
            return self._return(len(values), result)
        if name in ("LCMapStringA", "LCMapStringW", "MultiByteToWideChar", "WideCharToMultiByte"):
            values = args(8 if name == "WideCharToMultiByte" else 6)
            locale, flags, src, count, dst, capacity = values[:6]
            wide_input = name in ("LCMapStringW", "WideCharToMultiByte")
            wide_output = name in ("LCMapStringW", "MultiByteToWideChar")
            source = self._input(src, signed(count), wide_input)
            if capacity > 32768:
                raise ValueError("original NLS output exceeds bound")
            unit = 2 if wide_output else 1
            data = ctypes.create_string_buffer(max(capacity * unit, 1))
            call_args = [locale, flags, source, signed(count), data if dst else None, capacity]
            if name == "WideCharToMultiByte":
                default_char, used = values[6:]
                default = self._input(default_char, 1) if default_char else None
                used_value = ctypes.c_int(0)
                call_args += [default, ctypes.byref(used_value) if used else None]
            result = getattr(self.kernel, name)(*call_args)
            if result and dst:
                output(dst, data, result * unit)
            if name == "WideCharToMultiByte" and values[7]:
                uc.mem_write(values[7], struct.pack("<I", used_value.value))
            return self._return(len(values), result)
        if name in ("GetLocaleInfoA", "GetLocaleInfoW"):
            locale, kind, dst, capacity = args(4)
            if capacity > 32768:
                raise ValueError("original locale output exceeds bound")
            unit = 2 if name.endswith("W") else 1
            data = ctypes.create_string_buffer(max(capacity * unit, 1))
            result = getattr(self.kernel, name)(locale, kind, data if dst else None, capacity)
            if result and dst:
                output(dst, data, result * unit)
            return self._return(4, result)
        raise RuntimeError("unimplemented original Windows boundary: " + str(name))

    def string(self, text, index):
        data = text.encode("cp932", "strict") + b"\0"
        if len(data) > 4000 or index >= 16:
            raise ValueError("original string fixture exceeds bound")
        address = self.DATA + 0x40000 + index * 0x2000
        self.machine.mem_write(address, struct.pack("<3I", 64, 0, len(data)) + data + bytes(16))
        return address

    def string_result(self, tag, pointer):
        if tag != 2:
            raise ValueError("original result is not a string")
        length = self.read_u32(pointer + 8)
        if length < 1 or length > 65536:
            raise ValueError("original string length is invalid")
        raw = bytes(self.machine.mem_read(pointer + 12, length))
        if not raw.endswith(b"\0"):
            raise ValueError("original string result lacks terminator")
        return raw[:-1].decode("cp932", "strict")
