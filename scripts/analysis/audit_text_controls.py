"""Inventory text controls in every archive member, including branch bodies."""
from __future__ import annotations

from collections import Counter
import json
from pathlib import Path
import re
import sys

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/"convert"))
from majiro_arc import Archive
from majiro_mjo import Script,opcode_table
from port_common import ROOT,archive_dir,timestamp,write_json


def run():
    inventory=json.loads((ROOT/"reports/majiro_samples.json").read_text(encoding="utf-8"))
    runtime=(ROOT/"player/runtime/majiro_text.lua").read_text(encoding="utf-8")
    handlers=set(re.findall(r"command=='(.)'",runtime))
    definitions=opcode_table()
    report={"schema":1,"timestamp":timestamp(),"samples":[],"parse_errors":[],
        "runtime_control_branches":sorted(handlers),"members":0,
        "scope":"all MJO archive members and every instruction; inactive overlays retained separately",
        "limits":["handler presence is not semantic validation",
                  "raw in-string parser controls and generated commands require executable validation",
                  "loose translation overlays are not part of this archive-only inventory"]}
    missing=Counter()
    for game in inventory["games"]:
        counts=Counter();examples={};parsed=0
        for arc in game["archives"]:
            archive=Archive(Path(game["directory"])/arc["name"])
            if archive.index_sha256!=arc["index_sha256"]:raise ValueError("sample archive index changed")
            for member in archive.members:
                if member.extension!=".mjo":continue
                script=Script(member.name,member.read(),opcodes=definitions);parsed+=1
                for ins in script.instructions:
                    if ins.name!="ctrl":continue
                    control=ins.operands[0];command=control[:1]
                    counts[command]+=1
                    references=examples.setdefault(command,[])
                    if len(references)<4:references.append({"archive":arc["name"],"member":member.name,
                        "script_sha256":script.sha256,"offset":ins.offset})
        absent={command:count for command,count in counts.items()if command not in handlers}
        missing.update(absent);report["members"]+=parsed
        report["samples"].append({"sample":Path(game["directory"]).name,"parsed_members":parsed,
            "controls":dict(sorted(counts.items())),"missing_runtime_branches":absent,
            "examples":examples})
        print(f"{Path(game['directory']).name}: {parsed} members, {sum(counts.values())} controls, "
              f"{sum(absent.values())} lack a runtime branch",flush=True)
    report["missing_runtime_branches"]=dict(missing)
    report["status"]="passed"if not missing else"gaps_found"
    folder=archive_dir("text_control_inventory")
    write_json(folder/(folder.name[:8]+"_text_controls.json"),report)
    write_json(ROOT/"reports/text_control_inventory.json",report)
    return 0 if not missing else 1


if __name__=="__main__":raise SystemExit(run())
