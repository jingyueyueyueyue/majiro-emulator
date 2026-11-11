"""Compare hover handoff with the actual original save-menu input dispatcher."""
from pathlib import Path
import struct
import sys

import unicorn
from unicorn import x86_const as reg

sys.path[:0] = [str(Path(__file__).resolve().parents[1] / "convert")]
from port_common import ROOT, archive_dir, read_json, timestamp, write_json
from original_opcode import OpcodeOracle
from native_function import NativeFunctions
from test_menu_focus_semantics import HARNESS
from lupa.lua51 import LuaRuntime


def original_transitions(sample, folder):
    assert sample["exe_sha256"] == "139d1559bd83da7fa54cc8c4d30ccd45401bfba5e542c4762a5cf0e1e3b539a0"
    oracle = OpcodeOracle(sample)
    machine = oracle.machine
    # These addresses belong to the pinned magical_kanon2 executable. Preserve
    # the original area scan and state writes; intercept only host sampling,
    # integer-stack transport and the source-function invocation boundary.
    rows = NativeFunctions(oracle).body(0x476f40, maximum_bytes=2000)
    (folder / "original_dispatch_disassembly.txt").write_text(
        "\n".join(f"{row.address:08x} {row.mnemonic:9} {row.op_str}" for row in rows),
        encoding="utf8")
    nodes = [oracle.DATA + 0x80000, oracle.DATA + 0x80100]
    invocations = []
    pushed = []
    pointer = [12, 15]
    hooks = []

    def boundary(uc, address, size, unused):
        esp = uc.reg_read(reg.UC_X86_REG_ESP)
        if address == 0x460780:
            value = pointer[0]
        elif address == 0x460790:
            value = pointer[1]
        elif address == 0x463640:
            pushed.append(oracle.read_u32(esp + 4))
            value = 0
        elif address == 0x455cb0:
            invocations.append({"callback": oracle.read_u32(esp + 4),
                                "argc": oracle.read_u32(esp + 8),
                                "parameters_pushed": pushed[-5:]})
            value = 0
        else:
            value = 0
        uc.reg_write(reg.UC_X86_REG_EAX, value)
        uc.reg_write(reg.UC_X86_REG_EIP, oracle.read_u32(esp))
        uc.reg_write(reg.UC_X86_REG_ESP, esp + 4)

    for address in (0x463640, 0x44d9e0, 0x460780, 0x460790,
                    0x460840, 0x461170, 0x455cb0):
        hooks.append(machine.hook_add(unicorn.UC_HOOK_CODE, boundary,
                                      begin=address, end=address))
    stop = oracle.DATA + 0xff000

    def check():
        stack = oracle.STACK + 0x8000
        machine.mem_write(stack, struct.pack("<3I", stop, 0, 0))
        machine.reg_write(reg.UC_X86_REG_ESP, stack)
        machine.emu_start(0x476f40, stop, count=10000, timeout=1000000)
        assert machine.reg_read(reg.UC_X86_REG_EIP) == stop

    results = []
    try:
        for previous, target in ((1, 0), (0, 1), (1, None), (None, 0)):
            machine.mem_write(oracle.context, bytes(0x5100))
            machine.mem_write(oracle.frame, bytes(0x80))
            oracle.write_u32(oracle.context_global, oracle.context)
            oracle.write_u32(oracle.frame_global, oracle.frame)
            oracle.write_u32(oracle.context + 0x502c, nodes[0])
            for i, address in enumerate(nodes):
                data = [i + 1, 10 + i * 30, 10, 25, 30,
                        100 + i, 300 + i, 200 + i,
                        int(i == previous), 0x7fffffff, 0, 0,
                        nodes[i + 1] if i == 0 else 0]
                machine.mem_write(address, struct.pack("<13I", *data))
            pointer[:] = [12 + (target or 0) * 30, 15] if target is not None else [1000, 1000]
            invocations.clear()
            pushed.clear()
            samples = []
            for _ in range(3):
                before = len(invocations)
                check()
                samples.append([v["callback"] for v in invocations[before:]])
            expected = ([[300 + previous], [100 + target], []]
                        if previous is not None and target is not None else
                        [[300 + previous], [], []] if previous is not None else
                        [[100 + target], [], []])
            assert samples == expected, (previous, target, samples, expected)
            results.append({"previous": previous, "target": target,
                            "callbacks_per_check": samples,
                            "invocations": list(invocations)})
    finally:
        for hook in hooks:
            machine.hook_del(hook)
    return results


def runtime_transitions(input_source=None):
    results = []
    for previous, target in ((1, 0), (0, 1), (1, None), (None, 0)):
        lua = LuaRuntime(unpack_returned_tuples=True)
        lua.globals()["VM_SOURCE"] = (ROOT / "player/runtime/majiro_vm.lua").read_text("utf8")
        lua.globals()["INPUT_SOURCE"] = input_source or (ROOT / "player/runtime/majiro_input.lua").read_text("utf8")
        lua.globals()["PREVIOUS"] = previous + 1 if previous is not None else 0
        lua.globals()["TARGET"] = target + 1 if target is not None else 0
        lua.execute(HARNESS + """
          setarea(1);setarea(2)
          if PREVIOUS~=0 then thread.areas[PREVIOUS].inside=true end
          local x=TARGET==0 and 1000 or 12+(TARGET-1)*30
          poll({x=x,y=15})
          local expected={}
          if PREVIOUS~=0 then expected[#expected+1]=30+PREVIOUS-1 end
          if TARGET~=0 then expected[#expected+1]=10+TARGET-1 end
          for i=1,3 do
            calls={};check()
            local wanted=expected[i]
            assert(#calls==(wanted and 1 or 0),'hover must schedule one original transition per check')
            if wanted then assert(calls[1].offset==wanted,'leave must precede entering the next area')end
          end
        """)
        results.append({"previous": previous, "target": target, "status": "passed"})
    return results


def main():
    folder = archive_dir("hover_transition_order")
    sample = next(s for s in read_json(ROOT / "reports/opcode_dispatch.json")["samples"]
                  if s["sample"] == "magical_kanon2")
    report = {"timestamp": timestamp(), "status": "running",
              "exe_sha256": sample["exe_sha256"], "device_accessed": False,
              "evidence": str(folder.relative_to(ROOT)),
              "scope": "original native pointer leave/enter handoff; callbacks are captured at the dispatch boundary"}
    try:
        report["original"] = original_transitions(sample, folder)
        report["runtime"] = runtime_transitions()
        report["artemis_runtime"] = runtime_transitions(
            (ROOT / "runtime/majiro_input.lua").read_text("utf8"))
        source = (ROOT / "player/runtime/majiro_input.lua").read_text("utf8")
        start = source.index("    -- Original areaevent_check")
        end = source.index("    if not keyboard_dispatch and not leaving and active and", start)
        old = source[:start] + """    local leaving=false
    for i,area in ipairs(t.areas)do
      local inside=i==active
      if inside and not area.inside then queue(area,6)
      elseif not inside and area.inside then queue(area,7)end
      area.inside=inside
    end
""" + source[end:]
        try:
            runtime_transitions(old)
        except Exception as error:
            report["negative_control"] = {
                "old_list_order_rejected": True, "error": str(error)}
        else:
            raise AssertionError("old hover ordering unexpectedly passed")
        report["status"] = "passed"
    except Exception as error:
        report.update(status="failed", error=str(error))
        raise
    finally:
        write_json(folder / "report.json", report)
        write_json(ROOT / "reports/hover_transition_order.json", report)
    print("Hover transition order: 4 original dispatcher cases and 4 shared-runtime cases passed")


if __name__ == "__main__":
    main()
