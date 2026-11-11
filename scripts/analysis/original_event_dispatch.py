"""Execute original event routing, context lookup and frame creation unchanged."""
import struct
import zlib
from unicorn import x86_const as reg
from original_opcode import OpcodeOracle


def original_frame_case(sample, owners, target=None, wants=False):
    assert sample["exe_sha256"] == "45c8a1b5ea39a552629e637e4435b9fa9639978f40f3c164a24867c739d70075"
    oracle = OpcodeOracle(sample)
    uc = oracle.machine
    contexts = {1: oracle.DATA + 0x10000, 2: oracle.DATA + 0x20000}
    values = {1: oracle.DATA + 0x30000, 2: oracle.DATA + 0x40000}
    event = oracle.DATA + 0x50000
    for identifier, address in contexts.items():
        uc.mem_write(address, bytes(0x6000))
        oracle.write_u32(address + 4, identifier)
        oracle.write_u32(address + 0x10, 256)
        oracle.write_u32(address + 0x24, values[identifier])
        oracle.write_u32(address + 0x28, 0x440000 + identifier * 0x100)
        oracle.write_u32(address + 0x28 + 0x24, 123456)
    oracle.write_u32(contexts[1] + 0x1c, contexts[2])
    oracle.write_u32(0x541510, contexts[1])
    oracle.write_u32(oracle.context_global, contexts[1])
    oracle.write_u32(oracle.frame_global, contexts[1] + 0x28)
    oracle.write_u32(0x4c1c9c, event)
    target_pcs = []
    for index, owner in enumerate(owners):
        pc = 0x440000 + index * 0x1000
        target_pcs.append(pc)
        node = [owner, 0, zlib.crc32(b"SAVEMENU"), pc,
                event + (index - 1) * 0x40 if index else 0,
                event + (index + 1) * 0x40 if index + 1 < len(owners) else 0]
        uc.mem_write(event + index * 0x40, struct.pack("<6I", *node))
    oracle.write_u32(0x4c1ca0, event + (len(owners) - 1) * 0x40)
    stop, stack = oracle.DATA + 0xff000, oracle.STACK + 0x8000
    parameters = ([zlib.crc32(b"SAVEMENU"), 0, int(wants)] if target is None else
                  [zlib.crc32(b"SAVEMENU"), target, 0, int(wants)])
    uc.mem_write(stack, struct.pack("<" + "I" * (len(parameters) + 1), stop, *parameters))
    uc.reg_write(reg.UC_X86_REG_ESP, stack)
    uc.emu_start(0x40f660 if target is None else 0x40f820,
                 stop, count=50000, timeout=1000000)
    assert uc.reg_read(reg.UC_X86_REG_EIP) == stop
    observed = []
    for owner, address in contexts.items():
        depth = oracle.read_u32(address)
        pcs = [oracle.read_u32(address + frame * oracle.frame_stride + 0x28)
               for frame in range(1, depth + 1)]
        expected = ([pc for who, pc in zip(owners, target_pcs) if who == owner]
                    if target is None else list(reversed(target_pcs)) if target == owner else [])
        assert pcs == expected
        assert oracle.read_u32(address + 0x28 + 0x24) == 123456
        observed.append({"owner": owner, "depth": depth, "pushed_callback_pcs": pcs})
    assert oracle.read_u32(oracle.context_global) == contexts[1]
    assert oracle.read_u32(contexts[1] + 0x28 + 8) == int(wants)
    if wants:
        assert oracle.read_u32(values[1] + 12) == 0
    return {"owners": owners, "target": target, "wants": wants,
            "contexts": observed, "source_result_count": int(wants)}
