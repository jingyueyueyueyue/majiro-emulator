"""Check active translation provenance, display-only substitution and tunnel round trips."""
from __future__ import annotations
from pathlib import Path
import os
import sys
from lua_bridge import create_lua
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/"convert"))
from port_common import ROOT,read_json,write_json,lua_value
from majiro_patch import Patch
from majiro_mjo import Script
from proofread_text import Overrides


def run():
    patch=Patch()
    raw=b"a\x81\x01\x81\x02\x81\x03"
    assert patch.encode(patch.decode(raw))==raw
    lua=create_lua()
    data=lua.execute((ROOT/"resources/scripts/translation.lua").read_text(encoding="utf-8"))
    display=lua.execute((ROOT/"runtime/majiro_localization.lua").read_text(encoding="utf-8"))["new"](
        data,lambda text:text.encode("cp932"))["display"]
    mapping=read_json(ROOT/"localization/character_map.json")
    matched=0;first=None
    reviewed=Overrides()
    for member in patch.members:
        if member.extension!=".mjo":continue
        script=Script(member.name,member.read(),string_decoder=patch.decode,
                      address_repairs=patch.repairs(member.name,member.sha256),
                      stack_repairs=patch.preludes(member.name,member.sha256))
        expected_data=reviewed.apply_script(script,script.runtime_data())
        code=expected_data["code"]
        assert len({row[0]for row in code})==len(code)
        runtime=lua.execute((ROOT/"resources/scripts"/(script.id+".lua")).read_text(encoding="utf-8"))
        assert (ROOT/"resources/scripts"/(script.id+".lua")).read_text(encoding="utf-8")==(
            "return "+lua_value(expected_data)+"\n")
        assert runtime["source_sha256"]==member.sha256
        assert len(list(runtime["code"].values()))==len(code)
        assert os.path.samefile(ROOT/"localization/scripts"/(script.id+".lua"),
                                ROOT/"resources/scripts"/(script.id+".lua"))
        matched+=1
        if member.name.startswith("00")and first is None:
            line=next(row.operands[0]for row in script.instructions if row.name=="text")
            expected="".join(mapping.get(c,c)for c in line)
            source=next(row for row in script.instructions if row.name=="text")
            from proofread_text import script_id
            corrected=reviewed.entries.get(script_id(script.id,source.offset,0))
            if corrected:
                expected=corrected["value"]
                assert display(reviewed.encode(expected))==expected
            else:
                assert display(line)==expected and expected!=line
            first=expected
    report={"status":"passed","scripts_using_patch":matched,"script_files_shared_by_hardlink":matched,
        "first_display_line":first,"tunnel_round_trip":"passed","zip_extracted_bytes":0}
    write_json(ROOT/"reports/translation_tests.json",report)
    print("translation:",matched,"active scripts; first line:",first)
    return report


if __name__=="__main__":
    run()
