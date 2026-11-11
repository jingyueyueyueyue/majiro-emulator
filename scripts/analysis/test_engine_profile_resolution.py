"""Verify exact/code identity agreement, code-only recognition and conflict rejection."""
import hashlib
import json
from pathlib import Path
import sys
from lua_bridge import create_lua,table_value
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/"convert"))
from port_common import ROOT,archive_dir,timestamp,write_json


def main():
    lua=create_lua()
    def module(name):
        return lua.execute((ROOT/"player"/name).read_text(encoding="utf8"))
    resolver=module("runtime/majiro_engine_identity.lua")
    exact=module("runtime/majiro_engine_profiles.lua")
    platform=module("runtime/majiro_platform_profiles.lua")
    external=module("runtime/majiro_external_profiles.lua")
    code=module("runtime/majiro_engine_code_profiles.lua")
    registry=json.loads((ROOT/"reports/engine_code_profiles.json").read_text(encoding="utf8"))
    checks=[]
    host=lua.table()
    host["movies"]=lambda:lua.table()
    host["identity"]=lambda:"a"*64
    host["asset_text"]=lambda name:(ROOT/"player"/name).read_text(encoding="utf8")
    host["member_names"]=lambda:lua.table_from(["fixture.env"])
    host["resource_text"]=lambda name:"CLIENT_WIDTH 640\nCLIENT_HEIGHT 480\n"
    lua.globals()["HOST"]=host
    configuration=module("runtime/player_configuration.lua")
    for row in registry["sources"]:
        hashes=lua.table_from([row["exe_sha256"],row["identity"]])
        found,controls,owner,matched=resolver["resolve"](hashes,exact,platform,external,code)
        assert table_value(found)==table_value(exact[row["exe_sha256"]]),row["sample"]
        fallback=resolver["resolve"](lua.table_from(["unknown-new-file-sha",row["identity"]]),
                                     exact,platform,external,code)[0]
        assert table_value(fallback)==table_value(found)
        host["engine_hashes"]=lambda identity=row["identity"]:lua.table_from(["unknown-new-file-sha",identity])
        configured=configuration["make"]()
        assert configured["numeric_profile"]==found["numeric"]
        assert configured["width"]==640 and configured["height"]==480
        assert table_value(configured["platform_controls"])==table_value(controls)
        checks.append({"sample":row["sample"],"exact_and_code_agree":True,"code_only_recognized":True,
                       "production_configuration_code_fallback":True})
    fixture=lua.eval("""function(resolve)
      local p={numeric='x87',minmax_arguments=2,text_controls={legacy=true}}
      local platforms={whole={mouse_move='legacy_identity'}}
      local code={schema=1,profiles={code=p},platform={code={mouse_move='legacy_identity'}},external={}}
      local profiles={whole=p}
      assert(not pcall(resolve,{'unknown'},profiles,platforms,{},code))
      local fields={
        function()code.profiles.code.numeric='sse32'end,
        function()code.profiles.code.text_controls.legacy=false end,
        function()code.profiles.code.extra=true end,
        function()code.platform.code.mouse_move='modern'end,
      }
      for _,change in ipairs(fields)do
        code.profiles.code={numeric='x87',minmax_arguments=2,text_controls={legacy=true}}
        code.platform.code={mouse_move='legacy_identity'}
        change();assert(not pcall(resolve,{'whole','code'},profiles,platforms,{},code))
      end
      code.profiles.code=p;code.platform.code={mouse_move='legacy_identity'}
      code.external.code={license='variant-a'}
      assert(not pcall(resolve,{'whole','code'},profiles,platforms,
        {whole={license='variant-b'}},code))
      return 6
    end""")
    rejection_count=fixture(resolver["resolve"])
    folder=archive_dir("engine_profile_resolution_tests")
    report={"schema":1,"timestamp":timestamp(),"status":"passed","build_cases":checks,
            "rejection_cases":rejection_count,"device_accessed":False,
            "source_sha256":{name:hashlib.sha256((ROOT/"player/runtime"/name).read_bytes()).hexdigest()
                             for name in("majiro_engine_identity.lua","player_configuration.lua")},
            "evidence":str(folder.relative_to(ROOT))}
    write_json(ROOT/"reports/engine_profile_resolution_tests.json",report)
    write_json(folder/"20261110_engine_profile_resolution_tests.json",report)
    print("Engine profile resolution:",len(checks),"verified builds and",rejection_count,"rejections passed")


if __name__=="__main__":main()
