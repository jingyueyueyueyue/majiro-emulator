"""Recover only behavior needed by comparison fixtures, without platform setup.

These values never enter production engine configuration. An unrelated screen
or wrapper fixture limitation must not disable independent numeric comparisons.
"""
from functools import lru_cache
import struct

from original_opcode import OpcodeOracle
from original_strings import StringOracle


@lru_cache(maxsize=8)
def recover(path, identity, sample_name):
    from port_common import ROOT, read_json
    sample = next(s for s in read_json(ROOT / "reports/opcode_dispatch.json")["samples"]
                  if s["exe_sha256"] == identity and s["path"] == path)
    entries = next(s["entries"] for s in read_json(ROOT / "reports/syscall_dispatch.json")["samples"]
                   if s["exe_sha256"] == identity)
    observed, unavailable, profile = {}, [], {}
    oracle = OpcodeOracle(sample)
    probes = {
        "real_integer_rounding": ("ceq.r", [(0, 16777217), (1, 0x4b800000)]),
        "unordered_equality": ("ceq.r", [(0, 1), (1, 0x7fc00000)]),
        "infinite_to_integer": ("conv.i", [(1, 0x7f800000)]),
    }
    for key, (name, values) in probes.items():
        observed[key] = oracle.execute(name, values)
    variants = {
        "x87": {"real_integer_rounding": [(0, 0)], "unordered_equality": [(0, 1)],
                "infinite_to_integer": [(0, 0)]},
        "sse32": {"real_integer_rounding": [(0, 1)], "unordered_equality": [(0, 0)],
                  "infinite_to_integer": [(0, 0x80000000)]},
    }
    matches = [key for key, expected in variants.items()
               if all(observed[name] == value for name, value in expected.items())]
    assert len(matches) == 1, "unclassified original numeric comparison profile"
    profile["numeric"] = matches[0]
    try:
        result = oracle.execute("syscall", [(0, -4), (0, 3), (0, 7)],
                                struct.pack("<IH", 0x3d053317, 3), budget=20000)
        assert result in ([(0, 3)], [(0, 0xfffffffc)]), result
        profile["minmax_arguments"] = 2 if result == [(0, 3)] else 0
        observed["minimum_three_values"] = result
    except Exception as error:
        unavailable.append({"field": "minmax_arguments", "reason": str(error)})
    strings = StringOracle(sample)
    left, right = (strings.string(text, index)
                   for index, text in enumerate((chr(0x391), chr(0x3b1))))
    result = strings.execute("ceq.s", [(2, left), (2, right)], budget=100000)
    assert result in ([(0, 1)], [(0, 0)]), result
    profile["string_case"] = "cp932" if result == [(0, 1)] else "latin_fullwidth"
    observed["greek_case_equality"] = result
    for name, texts, field in (("$atoi", ["+2147483648"], "integer_parse"),
                                ("$strstr", ["", ""], "empty_search_absent")):
        entry = next(e for e in entries if e["name"] == name)
        values = [(2, strings.string(text, i)) for i, text in enumerate(texts)]
        result = strings.execute("syscall", list(reversed(values)),
                                 struct.pack("<IH", int(entry["hash"], 16), len(values)),
                                 budget=200000)
        observed[name] = result
        if field == "integer_parse":
            assert result in ([(0, 0x80000000)], [(0, 0x7fffffff)]), result
            profile[field] = "wrap32" if result == [(0, 0x80000000)] else "saturate32"
        else:
            assert result in ([(0, 0xffffffff)], [(0, 0)]), result
            profile[field] = result == [(0, 0xffffffff)]
    left, right = strings.string("A", 0), strings.string("A", 1)
    pointer = strings.DATA + 0x50000
    strings.machine.mem_write(pointer, struct.pack("<4I", 0x11f91fd3, 2, 2, left))
    strings.execute("bge.case", [(2, right)], struct.pack("<i", 42),
                    context_fields={8: 1, 0x20: pointer}, budget=200000)
    destination = strings.read_u32(strings.frame) - (strings.operand - 2)
    assert destination in (6, 48), destination
    profile["case_string_relations"] = destination == 48
    observed["string_case_ge_equal_destination"] = destination
    return {"sample": sample_name, "exe_sha256": identity, "profile": profile,
            "probes": observed, "unavailable_fields": unavailable,
            "original_reference": oracle.reference_provenance,
            "scope": "original comparison-only numeric/string behavior; platform/graphics profile not asserted",
            "production_configuration_modified": False}
