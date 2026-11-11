"""Prepare data-only patch metadata from raw translation sources for the player."""
from __future__ import annotations
import argparse
import hashlib
import json
from pathlib import Path
import sys

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/"convert"))
from majiro_mjo import Script,opcode_table
from majiro_patch import Patch
from port_common import ROOT,atomic_bytes,lua_value,read_json,timestamp,write_json
from proofread_text import Overrides,script_id


def build(destination=None):
    patch=Patch();reviewed=Overrides();opcodes=opcode_table()
    data={"schema":1,"format":"Majiro raw translation patch metadata",
        "scripts":{},"configs":{},"files":{},"decode_tunnel":{pair.hex():char for pair,char in patch.tunnel.items()},
        "display":{"characters":reviewed.characters,"tunnel":reviewed.tunnel,
                   "labels":read_json(ROOT/"localization/ui_display.json")}}
    substitutions=0;members=0
    for member in patch.members:
        raw=member.read();sha=hashlib.sha256(raw).hexdigest();members+=1
        data["files"][member.name.lower()]=sha
        record={"sha256":sha}
        if member.extension==".cfg":
            data["configs"][member.name.lower()]=record;continue
        if member.extension!=".mjo":continue
        relocations=patch.repairs(member.name,sha)or[]
        preludes=patch.preludes(member.name,sha)or[]
        script=Script(member.name,raw,opcodes,string_decoder=patch.decode,
                      address_repairs=relocations,stack_repairs=preludes)
        record["relocations"]=relocations
        record["preludes"]=[{"offset":entry["offset"],"opcode":next(
            code for code,(name,encoding)in opcodes.items()if name==entry["next_opcode"]),
            "push_values":entry["push_values"]}for entry in preludes]
        replacements={}
        for ins in script.instructions:
            if ins.name not in("text","ldstr"):continue
            for operand,kind in enumerate(ins.encoding):
                if kind!="s":continue
                ident=script_id(script.id,ins.offset,operand)
                source={"kind":"script","name":member.name,"sha256":sha,"offset":ins.offset,
                        "operand":operand,"opcode":ins.name,"raw":ins.operands[operand]}
                value=reviewed.replacement(ident,source)
                if value is not None:
                    replacements.setdefault(ins.offset,[]).append(
                        {"operand":operand,"opcode":ins.opcode,"expected":ins.operands[operand],"value":value})
                    substitutions+=1
        record["text_replacements"]=replacements
        data["scripts"][member.name.lower()]=record
    reviewed.finish()
    destination=destination or ROOT/"localization/player/ruri/majiro_metadata.lua"
    encoded=("-- Data only: source hashes, verified repairs and text substitutions; no compiled MJO code.\nreturn "+
             lua_value(data)+"\n").encode("utf8")
    atomic_bytes(destination,encoded)
    report={"status":"passed","timestamp":timestamp(),"metadata":str(destination.relative_to(ROOT)),
            "sha256":hashlib.sha256(encoded).hexdigest(),"bytes":len(encoded),"raw_members_verified":members,
            "script_records":len(data["scripts"]),"config_records":len(data["configs"]),
            "text_substitutions":substitutions,"compiled_script_payloads":0,
            "source":"raw pinned patch and formal proofreading/relocation metadata"}
    write_json(ROOT/"reports/player_patch_metadata.json",report)
    print("raw player patch metadata:",members,"members,",substitutions,"text substitutions,",len(encoded),"bytes")
    return report


if __name__=="__main__":
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument("--output",type=Path)
    args=parser.parse_args();build(args.output)
