"""Bind verified original behavior profiles to resource-independent PE code identities."""
import json
from pathlib import Path
import sys
from pe_code_identity import code_identity
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/"convert"))
from port_common import ROOT,atomic_bytes,lua_value,read_json,sha256,timestamp,write_json
sys.path.insert(0,str(ROOT/"scripts/analysis"))
from lua_bridge import create_lua,table_value


def main():
    profiles=read_json(ROOT/"config/player_engine_profiles.json")
    entries=profiles["executables"]
    samples=read_json(ROOT/"reports/opcode_dispatch.json")["samples"]
    samples+=read_json(ROOT/"reports/new_sample_scan.json")["opcode_samples"]
    happy=read_json(ROOT/"reports/happy_engine_profile.json")
    samples=[s for s in samples if s["sample"]!="happy_princess"]+[
        {"sample":"happy_princess","path":happy["reference_path"],"exe_sha256":happy["exe_sha256"]}]
    lua=create_lua()
    platforms=table_value(lua.execute((ROOT/"player/runtime/majiro_platform_profiles.lua").read_text(encoding="utf8")))
    external=table_value(lua.execute((ROOT/"player/runtime/majiro_external_profiles.lua").read_text(encoding="utf8")))
    mapping={};platform_mapping={};external_mapping={};evidence=[]
    for sample in samples:
        path=Path(sample["path"])
        if sha256(path)!=sample["exe_sha256"]:raise ValueError("verified code input changed")
        profile=entries[sample["exe_sha256"]]
        identity=code_identity(path.read_bytes())
        key=identity["identity"]
        if key in mapping and mapping[key]!=profile:
            raise ValueError("identical engine implementation has conflicting recovered profiles")
        mapping[key]=profile
        platform_mapping[key]=platforms[sample["exe_sha256"]]
        if sample["exe_sha256"]in external:external_mapping[key]=external[sample["exe_sha256"]]
        evidence.append({"sample":sample["sample"],"exe_sha256":sample["exe_sha256"],
                         "source":str(path),**identity})
    write_json(ROOT/"config/player_engine_code_profiles.json",{
        "schema":1,"timestamp":timestamp(),"format":"MAJIRO-CODE-V1",
        "identities":mapping,"platform_identities":platform_mapping,"external_identities":external_mapping,
        "unknown_engine":"requires_profile_recovery"})
    atomic_bytes(ROOT/"player/runtime/majiro_engine_code_profiles.lua",(
        "-- Recovered implementation behavior; PE resources and timestamps excluded.\nreturn "+
        lua_value({"schema":1,"profiles":mapping,"platform":platform_mapping,"external":external_mapping})+
        "\n").encode("utf8"))
    report={"schema":1,"timestamp":timestamp(),"status":"passed","sources":evidence,
            "unique_implementation_identities":len(mapping),"device_accessed":False,
            "limits":"Code identity selects recovered behavior only; archives/translation/saves retain independent identities."}
    write_json(ROOT/"reports/engine_code_profiles.json",report)
    print("Verified PE code identities:",len(mapping),"from",len(evidence),"original builds")


if __name__=="__main__":main()
