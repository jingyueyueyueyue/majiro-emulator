"""Verify engine-format recognition without any executable fingerprint allowlist."""
from pathlib import Path
import sys
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/"convert"))
from lua_bridge import create_lua
from port_common import ROOT,sha256,timestamp,write_json
from inspect_engine_feature_paths import EVIDENCE


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
    # Explicit original-code evidence must override generic generation hints
    # and survive the real configuration builder, including false booleans.
    host["engine_requirements"]=lambda:lua.table_from({"family":"Majiro","archive_version":3,
        "script_signature":"MajiroObjX1.000","features":[
            "save:105","array:3d_legacy_stride","audio:extensions:ogg-wav",
            "screen:integer_conversion:x87","screen:cursor_literal_coordinates",
            "event:release_registers_hook","string:search_case_sensitive",
            "string:cp932","text:x_no_event","text:history_retains_message",
            "dialog:warning_retrycancel:53"]},recursive=True)
    configuration=lua.execute((ROOT/"player/runtime/player_configuration.lua").read_text(encoding="utf8"))["make"]()
    assert configuration["numeric_profile"]=="sse32"
    assert configuration["array_3d_legacy_stride"]is True
    assert configuration["audio_controls"]["extensions"][1]==".ogg"
    assert configuration["audio_controls"]["extensions"][2]==".wav"
    assert configuration["screen_controls"]["integer_conversion"]=="x87"
    assert configuration["screen_controls"]["cursor_reset_sentinel"]is False
    assert configuration["event_controls"]["release_registers_hook"]is True
    assert configuration["string_search_case_sensitive"]is True
    assert configuration["string_case"]=="cp932"
    assert configuration["text_controls"]["x_control_event"]is False
    assert configuration["text_controls"]["history_suppresses_message"]is False
    assert configuration["dialog_controls"]["warning_retrycancel_flags"]==53
    cases.append({"explicit_code_features_override_generation":True,"status":"passed"})
    profile=engine["detect"](lua.table_from({"family":"Majiro","archive_version":3,
        "script_signature":"MajiroObjX1.000",
        "features":["save:104","external:license:constant_zero"]},recursive=True))
    assert profile["external_controls"]["license"]=="native_constant_zero"
    cases.append({"constant_license_code_overrides_generation":True,"status":"passed"})
    for bad in({"family":"other","archive_version":3,"script_signature":"MajiroObjX1.000"},
                {"family":"Majiro","archive_version":4,"script_signature":"MajiroObjX1.000"},
                {"family":"Majiro","archive_version":3,"script_signature":"not-a-script"}):
        try:engine["detect"](lua.table_from(bad,recursive=True))
        except Exception:pass
        else:raise AssertionError("unsupported engine format accepted")
    folder=EVIDENCE/(timestamp().replace(":", "").replace("+", "_")+"_formats")
    folder.mkdir(parents=True,exist_ok=False)
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
