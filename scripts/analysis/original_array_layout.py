"""Classify native 3D element addressing using the unmodified load instruction."""
import struct


def legacy_3d_stride(oracle):
    pointer = oracle.DATA + 0x30000
    expected = []
    for indices in ((1, 0, 0), (0, 1, 0), (0, 0, 1), (1, 2, 3)):
        oracle.machine.mem_write(pointer, struct.pack("<5I", 64, 0, 2, 3, 4)
                                 + struct.pack("<24I", *range(24)))
        result = oracle.execute("ldelem", [(3, pointer), *((0, n) for n in indices)],
                                struct.pack("<HIh", 96 + 3 * 2048, 123, 0), budget=20000)
        assert len(result) == 2 and result[-1][0] == 0, result
        expected.append(result[-1][1])
    legacy = expected == [3, 4, 1, 14]
    assert legacy or expected == [12, 4, 1, 23], ("unclassified native 3D array stride", expected)
    oracle.machine.mem_write(pointer, struct.pack("<5I", 64, 0, 2, 3, 4)
                             + struct.pack("<24I", *range(24)))
    result = oracle.execute("stelemp.i", [(3, pointer), (0, 99), (0, 1), (0, 2), (0, 3)],
                            struct.pack("<HIh", 96 + 3 * 2048, 123, 0), budget=20000)
    assert result == [(3, pointer)] and oracle.read_u32(pointer + 20 + (14 if legacy else 23) * 4) == 99
    return legacy
