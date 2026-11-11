"""Recover generalized text-control discriminators from original dispatch CFGs."""
import json
from pathlib import Path
import sys

import capstone
from original_text import TextOracle

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "convert"))
from port_common import ROOT, archive_dir, timestamp, write_json


def run():
    samples=json.loads((ROOT/"reports/opcode_dispatch.json").read_text(encoding="utf8"))["samples"]
    dispatch=json.loads((ROOT/"reports/syscall_dispatch.json").read_text(encoding="utf8"))["samples"]
    rows=[]
    for sample in samples:
        entries=next(row["entries"]for row in dispatch if row["exe_sha256"]==sample["exe_sha256"])
        oracle=TextOracle(sample,entries)
        profile=oracle.profile()
        f=oracle.functions
        getter=oracle.entry_body("$get_last_msg$")
        message_buffer=next(op.imm for row in getter if row.mnemonic in("push","mov")
                            for op in row.operands if op.type==capstone.CS_OP_IMM and op.imm>=0x400000)
        direct=f.body(oracle.proc_va)
        reachable={oracle.proc_va:direct}
        for row in direct:
            if row.mnemonic=="call"and row.operands[0].type==capstone.CS_OP_IMM:
                at=row.operands[0].imm
                try:
                    reachable[at]=f.body(at)
                except ValueError:
                    # Large diagnostics/CRT helpers are outside the text
                    # state branch being classified.
                    continue
        history_reads=[]
        message_writes=[]
        for at,body in reachable.items():
            for index,row in enumerate(body):
                if any(op.type==capstone.CS_OP_MEM and not op.mem.base and not op.mem.index
                       and op.mem.disp==oracle.history_slot for op in row.operands):
                    history_reads.append({"function":hex(at),"address":hex(row.address),
                                          "context":[f"{r.address:08x} {r.mnemonic} {r.op_str}"
                                                     for r in body[max(0,index-3):index+8]]})
                if any((op.type==capstone.CS_OP_IMM and op.imm==message_buffer)or
                       (op.type==capstone.CS_OP_MEM and op.mem.disp==message_buffer)for op in row.operands):
                    message_writes.append({"function":hex(at),"address":hex(row.address),
                                           "context":[f"{r.address:08x} {r.mnemonic} {r.op_str}"
                                                      for r in body[max(0,index-18):index+12]]})
        target=oracle.ctrl_cases.get("x")
        body=f.body(target,minimum_address=oracle.ctrl_va) if target else []
        # The same switch target as unused controls denotes the default
        # no-op case; compare cases instead of matching executable names.
        shared=[key for key,value in oracle.ctrl_cases.items() if value==target]
        rows.append({"sample":sample["sample"],"exe_sha256":sample["exe_sha256"],
                     "profile":profile,"control_dispatch":hex(oracle.ctrl_va),
                     "x_case":hex(target)if target else None,"x_shared_cases":shared,
                     "x_case_instructions":[f"{r.address:08x} {r.mnemonic} {r.op_str}"for r in body[:100]],
                     "proc":hex(oracle.proc_va),"history_depth_slot":hex(oracle.history_slot),
                     "history_depth_reads":history_reads,"message_buffer":hex(message_buffer),
                     "message_buffer_references":message_writes})
        print(sample["sample"],profile.get("x_control_event",True),
              profile.get("history_suppresses_message",True),shared,len(history_reads),flush=True)
    report={"status":"passed","timestamp":timestamp(),"samples":rows,
            "scope":"Original runtime execution classifies flags; dispatch CFG and depth-read instruction evidence for production feature recovery"}
    folder=archive_dir("text_features")
    write_json(folder/"report.json",report)
    write_json(ROOT/"reports/player_text_feature_evidence.json",report)
    return report


if __name__=="__main__":
    run()
