"""Compare original line bitmap ownership with main/child/independent VM threads."""
from pathlib import Path
import struct
import sys

sys.path[:0] = [str(Path(__file__).resolve().parents[1] / "convert")]
from port_common import ROOT, archive_dir, read_json, timestamp, write_json
from original_opcode import OpcodeOracle
from audit_player_semantics import registered_providers


def main():
    samples = read_json(ROOT / "reports/opcode_dispatch.json")["samples"]
    rows = []
    for sample in samples:
        oracle = OpcodeOracle(sample)
        for owner in (0, 1, 2):
            for already_read in (False, True):
                record, bitmap = oracle.DATA + 0x40000, oracle.DATA + 0x41000
                oracle.machine.mem_write(record, bytes(0x100))
                oracle.write_u32(record + 0x88, bitmap)
                oracle.machine.mem_write(bitmap, bytes([8 if already_read else 0]) + bytes(31))
                oracle.execute("line", [], struct.pack("<H", 3), budget=30000,
                               context_fields={4: owner},
                               frame_fields={oracle.frame_stride - 12: record})
                original = bytes(oracle.machine.mem_read(bitmap, 1))[0] != 0
                lua, _, _, system = registered_providers()
                module = lua.execute((ROOT / "player/runtime/majiro_vm.lua").read_text("utf8"))
                ops = lua.execute((ROOT / "player/tables/opcodes.lua").read_text("utf8"))
                actual, current = lua.eval("""function(mod,ops,system,owner,read)
                  local script={name='marks.mjo',source_sha256='marks',main=0,line_count=8,
                    functions={{99,0}},code={{0,0x83a,3},{4,0x191},{6,0x82c,4}}}
                  local vm=mod.new{opcodes=ops,loader=function()return script end,
                    syscall=system.invoke,line_changed=system.line_changed}
                  vm:begin(script.name)
                  if owner~=0 then vm:select_thread(vm:spawn(99,{},owner==2))end
                  system.state.current_line_read=true
                  if read then system.state.progress.lines['marks:3']=true end
                  vm:step()
                  return system.state.progress.lines['marks:3']==true,system.state.current_line_read
                end""")(module, ops, system, owner, already_read)
                assert actual == original, (sample["sample"], owner, already_read, actual, original)
                assert current == (already_read if owner == 0 else True)
                rows.append({"sample": sample["sample"], "native_owner": owner,
                             "already_read": already_read, "marked_after": original})
        print(sample["sample"], "six original readmark owner cases passed", flush=True)
    folder = archive_dir("player_readmark_owners")
    report = {"timestamp": timestamp(), "status": "passed", "comparisons": len(rows), "cases": rows,
              "scope": "original native bitmap writes; production VM main/independent/child line callbacks",
              "device_accessed": False}
    write_json(folder / "report.json", report)
    write_json(ROOT / "reports/player_readmark_owners.json", report)


if __name__ == "__main__":
    main()
