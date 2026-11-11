"""Recover all original dialog variants without invoking a desktop window."""
from __future__ import annotations

import json
from pathlib import Path
import struct
import sys

from original_dialogs import DialogOracle

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/"convert"))
from port_common import ROOT,archive_dir,sha256,timestamp,write_json


def rendered(args):
    values=[]
    for value in args:
        if isinstance(value,str):values.append(value)
        elif isinstance(value,dict):
            values.append({3:"[INT_ARRAY]",4:"[FIXED_ARRAY]",5:"[STRING_ARRAY]"}[value["native_type"]])
        elif isinstance(value,float):
            rounded=struct.unpack("<f",struct.pack("<f",value))[0]
            values.append(f"{rounded:f}")
        else:values.append(str(value))
    return "".join(value+" "for value in values)


def run():
    evidence=archive_dir("dialog_recovery_tests")
    samples=json.loads((ROOT/"reports/opcode_dispatch.json").read_text(encoding="utf8"))["samples"]
    dispatch={row["sample"]:row["entries"]for row in
              json.loads((ROOT/"reports/syscall_dispatch.json").read_text(encoding="utf8"))["samples"]}
    signatures=ROOT/"reports/player_call_signatures.json"
    sites={row["sample"]:row["signatures"]for row in
           json.loads(signatures.read_text(encoding="utf8"))["samples"]}if signatures.exists()else{}
    report={"schema":1,"timestamp":timestamp(),"status":"running","samples":[],"failures":[],
            "scope":"all 15 native dialog wrappers, original Majiro argument concatenation, flags, "
                "production-mode guard and result conversion at terminal MessageBoxA boundary",
            "host_boundaries":["Win32 focus/flush/caption and MessageBoxA are recorded fixtures",
                "original CRT sprintf calls use host MSVCRT for fixed formats",
                "diagnostic log output is recorded without filesystem writes",
                "CRT single-thread storage uses bounded fixture memory"],
            "limits":["dialogs larger than the original fixed 2 KiB buffer remain a separate target",
                      "Android UI dispatch/lifecycle has not been implemented or tested here",
                      "original error-log file writing and caption customization are separate features"],
            "runtime_sources_modified_by_this_tool":False,"complete_engine_semantics":False}
    fixture_sets=[[],["message"],["100% literal",17],["A",-2147483648,3.25],
                  ["B",-1,0.0,"",2147483647],
                  list(range(11)),["A",1,1.5,"B",-2,-0.0,"C",3,1.234567,"D",4,5.25],
                  [{"native_type":3},{"native_type":4},{"native_type":5},"\u30c6\u30b9\u30c8"]]
    for sample in samples:
        oracle=DialogOracle(sample,dispatch[sample["sample"]])
        calls=[]
        for name,entry in oracle.entries.items():
            if not name.startswith("$dialog_")or"input"in name:continue
            call={"name":name,"hash":entry["hash"],"cases":0,"failures":0,
                  "argument_counts":sorted({len(args)for args in fixture_sets}),
                  "discard_modes":[False,True],"flags":set(),"icons":set(),
                  "buttons":set(),"response_map":{},"production_modes":{},
                  "script_signatures":[{"argc":site["argc"],"opcode":site["opcode"]}
                    for site in sites.get(sample["sample"],[])if site["hash"]==entry["hash"]]}
            for mode in(-1,0,1,2):
                for index,args in enumerate(fixture_sets):
                    # Each arity has retain/discard coverage. Extra modes
                    # classify the guard independently of arity/response.
                    answers=(1,2,4,6,7)if mode in(0,1)else(6,)
                    for answer in answers:
                        for discard in(False,True):
                            call["cases"]+=1;oracle.answer=answer
                            try:
                                actual=oracle.call_dialog(name,args,discard=discard,mode=mode)
                                error=name.startswith("$dialog_error_")
                                suppressed=error and mode==1
                                assert len(actual["dialogs"])==(0 if suppressed else 1),actual
                                call["production_modes"][str(mode)]={"dialog_suppressed":suppressed,
                                    "log_suppressed":not any(event.get("boundary")=="diagnostic_log"
                                        for event in actual["host_events"])}
                                if suppressed:
                                    assert actual["host_events"]==[],actual
                                    wanted=0 if name=="$dialog_error_ok"else 1
                                    call["suppressed_return"]=wanted
                                else:
                                    dialog=actual["dialogs"][0]
                                    expected=rendered(args)
                                    assert dialog["message"]==expected,(dialog["message"],expected)
                                    assert dialog["caption"]==oracle.caption.decode("ascii")
                                    buttons=dialog["buttons"]
                                    wanted=int(answer==(1 if buttons==1 else 6 if buttons==4 else 4))if buttons else 0
                                    call["flags"].add(dialog["flags"])
                                    call["icons"].add(dialog["icon"]);call["buttons"].add(buttons)
                                    key=str(answer)
                                    old=call["response_map"].setdefault(key,wanted)
                                    assert old==wanted
                                    call.setdefault("formatted_examples",{})
                                    if str(len(args))not in call["formatted_examples"]:
                                        call["formatted_examples"][str(len(args))]=dialog["message_hex"]
                                assert actual["stack"]==([]if discard else[(0,wanted)]),actual
                            except Exception as error:
                                call["failures"]+=1
                                if len(report["failures"])<30:report["failures"].append({
                                    "sample":sample["sample"],"name":name,"mode":mode,
                                    "fixture":index,"argc":len(args),"answer":answer,
                                    "discard":discard,"reason":repr(error)})
            call["flags"]=sorted(call["flags"]);call["icons"]=sorted(call["icons"])
            call["buttons"]=sorted(call["buttons"])
            call["status"]="passed"if not call["failures"]else"failed"
            calls.append(call)
        report["samples"].append({"sample":sample["sample"],"exe_sha256":sample["exe_sha256"],
                                   "mode_global_va":hex(oracle.mode_slot),"calls":calls})
        print(f"{sample['sample']}: {sum(call['cases']for call in calls)} dialog recovery cases; "
              f"{sum(call['failures']for call in calls)} mismatches",flush=True)
        write_json(evidence/(evidence.name[:8]+"_dialog_progress.json"),report)
    report["comparisons"]=sum(call["cases"]for sample in report["samples"]for call in sample["calls"])
    report["mismatches"]=sum(call["failures"]for sample in report["samples"]for call in sample["calls"])
    report["test_sources"]={name:sha256(ROOT/"scripts/analysis"/name)for name in(
        "original_opcode.py","original_strings.py","original_calls.py","original_text.py",
        "native_function.py","original_dialogs.py","test_dialogs_recovery.py")}
    report["status"]="passed"if not report["mismatches"]else"failed"
    write_json(evidence/(evidence.name[:8]+"_dialog_recovery.json"),report)
    write_json(ROOT/"reports/dialog_recovery_tests.json",report)
    lines=["# Native dialog recovery","",f"Status: {report['status']}; {report['comparisons']} cases.",
        "","All ten recovered builds expose the same fifteen dialog names below.",
        "The input dialog is a separate interface and is not included.",
        "","| Name | Win32 flags | Buttons | Icon | Suppressed return |",
        "| --- | --- | --- | --- | --- |"]
    baseline=report["samples"][-1]["calls"]
    for call in baseline:
        lines.append(f"| {call['name']} | {call['flags']} | {call['buttons']} | "
                     f"{call['icons']} | {call.get('suppressed_return','not suppressed')} |")
    lines+=["","Formatting: concatenate arguments in script order, append an ASCII space after each.",
        "Integers use signed decimal; float32 values are widened and rendered with six fractional digits.",
        "Strings remain literal (including percent signs and Japanese CP932 bytes).",
        "Arrays render `[INT_ARRAY] `, `[FIXED_ARRAY] `, or `[STRING_ARRAY] ` without reading elements.",
        "Zero arguments produce an empty message. The dialog caption is the current game-window title.",
        "","Return conversion: OK variants always return 0. OK/Cancel returns 1 only for IDOK (1); "
        "Yes/No only for IDYES (6); Retry/Cancel only for IDRETRY (4). Other host answers produce 0.",
        "","Only `$dialog_error_*` checks production mode. Original mode value exactly 1 suppresses "
        "both diagnostic logging and dialog. Suppressed OK returns 0; suppressed choice variants return 1.",
        "Other mode values (-1, 0, 2 tested) display normally. Ordinary/ask/warning variants show in all tested modes.",
        "The original `$dialog_warning_retrycancel` uses question icon 0x20, while other warning variants use 0x30.",
        "","Observed MJO arities include 1, 2, 3, 5, 11 and 12. Recovery fixtures cover 0, 1, 2, 3, 4, 5, 11, 12 "
        "arguments and both syscall result-retention modes across every build.",
        "","Windows focus/flush/window-title and MessageBoxA are bounded host fixtures. Original Majiro "
        "concatenation and result/guard instructions run unchanged. CRT fixed-format conversions use host MSVCRT. "
        "Error diagnostic log output is isolated without filesystem writes.",
        "Android UI/lifecycle and original fixed-buffer overflow behavior remain unverified.",""]
    text="\n".join(lines)
    (ROOT/"reports/dialog_recovery.md").write_text(text,encoding="utf8")
    (evidence/(evidence.name[:8]+"_dialog_recovery.md")).write_text(text,encoding="utf8")
    return 0 if report["status"]=="passed"else 1


if __name__=="__main__":raise SystemExit(run())
