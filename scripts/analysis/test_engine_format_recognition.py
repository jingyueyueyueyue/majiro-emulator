"""Verify engine-format recognition without any executable fingerprint allowlist."""
from pathlib import Path
import sys
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/"convert"))
from lua_bridge import create_lua
from port_common import ROOT,archive_dir,sha256,timestamp,write_json


def main():
    lua=create_lua()
    source=ROOT/"player/runtime/majiro_engine.lua"
    engine=lua.execute(source.read_text(encoding="utf8"))
    cases=[]
    for archive in(1,2,3):
        for version,numeric in((102,"x87"),(103,"x87"),(104,"x87"),(105,"sse32")):
            requirements={"family":"Majiro","archive_version":archive,
                "script_signature":"MajiroObjX1.000","features":["save:"+str(version)]}
            profile=engine["detect"](lua.table_from(requirements,recursive=True))
            assert profile["numeric"]==numeric
            assert profile["text_controls"]["uppercase_pause_event"]==(version>=104)
            cases.append({"archive":archive,"save_generation":version,"status":"passed"})
    for signature in("MajiroObjX1.000","MajiroObjV1.000","MjPlainBytecode"):
        profile=engine["detect"](lua.table_from({"family":"Majiro","archive_version":3,
            "script_signature":signature,"features":[]},recursive=True))
        assert profile["numeric"]=="sse32"
        cases.append({"no_executable_present":True,"signature":signature,"status":"passed"})
    host=lua.table()
    host["movies"]=lambda:lua.table();host["identity"]=lambda:"a"*64
    def asset(path):
        assert "profiles"not in path and"identity"not in path,"fingerprint registry read"
        return(ROOT/"player"/path).read_text(encoding="utf8")
    host["asset_text"]=asset
    host["member_names"]=lambda:lua.table_from(["majiro.env"])
    host["resource_text"]=lambda name:"CLIENT_WIDTH 800\nCLIENT_HEIGHT 600\n"
    host["engine_hashes"]=lambda:(_ for _ in()).throw(AssertionError("EXE fingerprints requested"))
    host["engine_requirements"]=lambda:lua.table_from({"family":"Majiro","archive_version":3,
        "script_signature":"MajiroObjX1.000","features":["save:104"]},recursive=True)
    lua.globals()["HOST"]=host
    configuration=lua.execute((ROOT/"player/runtime/player_configuration.lua").read_text(encoding="utf8"))["make"]()
    assert configuration["numeric_profile"]=="x87"and configuration["engine_generation"]==104
    assert configuration["width"]==800 and configuration["height"]==600
    for bad in({"family":"other","archive_version":3,"script_signature":"MajiroObjX1.000"},
                {"family":"Majiro","archive_version":4,"script_signature":"MajiroObjX1.000"},
                {"family":"Majiro","archive_version":3,"script_signature":"not-a-script"}):
        try:engine["detect"](lua.table_from(bad,recursive=True))
        except Exception:pass
        else:raise AssertionError("unsupported engine format accepted")
    folder=archive_dir("engine_format_recognition")
    report={"schema":1,"status":"passed","timestamp":timestamp(),"cases":cases,
        "exe_fingerprint_validation":False,"exe_required":False,"format_rejection_cases":3,
        "production_configuration_without_hash_registry":True,"device_accessed":False,
        "source_sha256":{str(p.relative_to(ROOT)):sha256(p)for p in(
            source,ROOT/"player/runtime/player_configuration.lua")},
        "evidence":str(folder.relative_to(ROOT))}
    write_json(ROOT/"reports/engine_format_recognition_tests.json",report);write_json(folder/"report.json",report)
    print("Majiro format recognition:",len(cases),"version/format cases; EXE allowlist removed")
    return 0


if __name__=="__main__":raise SystemExit(main())
