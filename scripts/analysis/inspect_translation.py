"""Check translation structure and pin provable relocations against original control flow."""
from __future__ import annotations
from pathlib import Path
import sys
import difflib
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/"convert"))
from port_common import ROOT,read_json,write_json
from majiro_arc import Catalogue
from majiro_patch import Patch
from majiro_mjo import Script,opcode_table


def run(write=False):
    patch=Patch();original=Catalogue(ROOT,include_patch=False);ops=opcode_table()
    relocations={};stack_repairs={};count=0
    for member in patch.members:
        if member.extension!=".mjo":continue
        source=Script(member.name,original.get(member.name).read(),ops)
        translated=Script(member.name,member.read(),ops,string_decoder=patch.decode,
                          inspect_invalid_targets=True)
        def key(row):
            return row.name,tuple(repr(v)for e,v in zip(row.encoding,row.operands)if e not in("s","j","c"))
        same=list(map(key,source.instructions))==list(map(key,translated.instructions))
        repairs=[]
        if same:
            mapping={a.offset:b.offset for a,b in zip(source.instructions,translated.instructions)}
            for a,b in zip(source.instructions,translated.instructions):
                expected=tuple(mapping[t]for t in a.targets)
                if expected!=b.targets:
                    if b.name!="br":raise ValueError("unexpected translation control-flow change")
                    repairs.append({"offset":b.offset,"old_target":b.targets[0],"new_target":expected[0],
                                    "original_offset":a.offset,"original_target":a.targets[0]})
        elif translated.invalid_targets:
            raise ValueError("cannot prove relocation for "+member.name)
        if repairs:
            relocations[member.name]={"patch_sha256":translated.sha256,
                "original_sha256":source.sha256,"proof":"all instruction opcodes and non-text operands match by ordinal",
                "repairs":repairs}
        def filtered(script):
            return [r for r in script.instructions if r.name not in("text","proc","line")
                    and not(r.name=="ctrl"and r.operands==("n",))]
        def control_key(row):
            return (row.name,row.operands)if row.name=="ctrl"else key(row)
        before,after=filtered(source),filtered(translated)
        alignment=difflib.SequenceMatcher(None,list(map(control_key,before)),
                                         list(map(control_key,after)),autojunk=False)
        numeric=[]
        for tag,i,j,k,l in alignment.get_opcodes():
            if tag=="equal":continue
            removed=before[i:j]
            if tag!="delete":raise ValueError("unexpected non-text translation edit: "+member.name)
            if all(r.name=="ldc.i"for r in removed):
                if k>=len(after):raise ValueError("missing translation prelude anchor")
                numeric.append({"offset":after[k].offset,"next_opcode":after[k].name,
                    "push_values":[r.operands[0]for r in removed],
                    "original_offsets":[r.offset for r in removed]})
            elif not(len(removed)==3 and [r.name for r in removed]==["ldstr","ldstr","callp"]
                     and removed[-1].operands==(832109825,0,2)):
                raise ValueError("unexplained translation instruction deletion: "+member.name)
        if numeric:
            stack_repairs[member.name]={"patch_sha256":translated.sha256,"original_sha256":source.sha256,
                "proof":"original non-text instruction alignment differs only by removed numeric pushes and translated ruby",
                "repairs":numeric}
        count+=1
    target=ROOT/"localization/relocations.json"
    if write:write_json(target,relocations)
    elif not target.exists()or read_json(target)!=relocations:
        raise ValueError("pinned translation relocation evidence changed")
    target=ROOT/"localization/stack_repairs.json"
    if write:write_json(target,stack_repairs)
    elif not target.exists()or read_json(target)!=stack_repairs:
        raise ValueError("pinned translation numeric prelude evidence changed")
    for member in patch.members:
        if member.extension==".mjo":
            record=relocations.get(member.name,{})
            Script(member.name,member.read(),ops,string_decoder=patch.decode,address_repairs=record.get("repairs"))
    report={"status":"passed","scripts":count,"relocated_branches":sum(len(r["repairs"])for r in relocations.values()),
        "restored_numeric_instructions":sum(len(r["push_values"])for s in stack_repairs.values()for r in s["repairs"]),
        "relocated_scripts":list(relocations),"raw_patch_modified":False,"patch_zip_extracted_bytes":0}
    write_json(ROOT/"reports/translation_check.json",report)
    print(report)
    return report


if __name__=="__main__":
    run("--write-relocations"in sys.argv)
