"""Recover behavior for newly supplied engines using bounded original x86 probes."""
from __future__ import annotations
import argparse
import json
from pathlib import Path
import sys
import traceback
from recover_numeric_profiles import recover
from original_platform_controls import PlatformOracle
from lua_bridge import create_lua, table_value

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/"convert"))
from port_common import ROOT,archive_dir,atomic_bytes,lua_value,read_json,sha256,timestamp,write_json

PROFILE_KEYS=("minmax_arguments","string_case","integer_parse","empty_search_absent",
              "case_string_relations","ultra_disable_returns_previous","ultra_fast_mode_available",
              "text_controls","voice_controls","screen_controls","graphics_controls","dialog_controls")


def verified_happy():
    path=ROOT/"reports/happy_engine_profile.json"
    if not path.exists():return None
    row=read_json(path);wrapper=read_json(ROOT/"reports/happy_wrapper_recovery.json")
    if(row.get("status")!="passed"or wrapper.get("status")!="passed"
       or row["exe_sha256"]!=wrapper["initialized_reference_sha256"]
       or sha256(Path(row["reference_path"]))!=row["exe_sha256"]
       or set(row["aliases"])!={wrapper["raw_embedded_sha256"],wrapper["container_sha256"]}):
        raise ValueError("Happy initialized engine profile provenance changed")
    row["profile_reference_exe_sha256"]=row["exe_sha256"]
    row["profile_evidence_file"]=str(path.relative_to(ROOT))
    row["exe_sha256"]=wrapper["raw_embedded_sha256"]
    return row


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--sample",action="append")
    parser.add_argument("--publish-only",action="store_true")
    args=parser.parse_args()
    scan=read_json(ROOT/"reports/new_sample_scan.json")
    entries={s["exe_sha256"]:s["entries"]for s in scan["syscall_samples"]}
    previous=ROOT/"reports/new_engine_profiles.json"
    report=read_json(previous)if previous.exists()else{
        "schema":1,"samples":[],"executables":{},"platform_executables":{}}
    report.update(timestamp=timestamp(),status="running",gameplay_started=False,device_accessed=False)
    evidence=archive_dir("new_engine_profiles")
    happy=verified_happy()
    for sample in ([]if args.publish_only else scan["opcode_samples"]):
        if args.sample and sample["sample"]not in args.sample:continue
        print("Recovering",sample["sample"],flush=True)
        try:
            row=happy if sample["sample"]=="happy_princess"and happy else recover(sample,entries[sample["exe_sha256"]])
            profile={key:row[key]for key in PROFILE_KEYS};profile["numeric"]=row["numeric_profile"]
            platform=row["platform_controls"]if row is happy else PlatformOracle(sample,entries[sample["exe_sha256"]]).profile()
            row["platform_controls"]=platform
            report["executables"][row["exe_sha256"]]=profile
            report["platform_executables"][row["exe_sha256"]]=platform
            # A wrapper/container may carry the original engine unchanged.
            for game in scan["games"]:
                for exe in game.get("executables",[]):
                    if exe.get("embedded_engine_sha256")==row["exe_sha256"]:
                        report["executables"][exe["sha256"]]=profile
                        report["platform_executables"][exe["sha256"]]=platform
            row["status"]="passed"
            print(sample["sample"],row["numeric_profile"],row["screen_controls"],flush=True)
        except Exception as error:
            row={"sample":sample["sample"],"exe_sha256":sample["exe_sha256"],
                 "status":"failed","reason":repr(error)}
            (evidence/(sample["sample"]+".txt")).write_text(traceback.format_exc(),encoding="utf8")
            print(sample["sample"],"FAILED",repr(error),flush=True)
        report["samples"]=[r for r in report["samples"]if r["exe_sha256"]!=sample["exe_sha256"]]+[row]
        write_json(previous,report)
    if happy:
        report["samples"]=[r for r in report["samples"]if r["sample"]!="happy_princess"]+[happy]
        profile={key:happy[key]for key in PROFILE_KEYS};profile["numeric"]=happy["numeric_profile"]
        for identity in set(happy["aliases"]+[happy["exe_sha256"],happy["profile_reference_exe_sha256"]]):
            report["executables"][identity]=profile
            report["platform_executables"][identity]=happy["platform_controls"]
    required={row["sample"]for row in scan["opcode_samples"]}
    completed={row["sample"]for row in report["samples"]if row["status"]=="passed"}
    failures=required-completed
    report.update(status="failed"if failures else"passed",evidence=str(evidence.relative_to(ROOT)))
    write_json(previous,report);write_json(evidence/"profiles.json",report)
    if failures:return 1
    baseline=read_json(ROOT/"config/player_engine_profiles.json")
    baseline["executables"].update(report["executables"])
    write_json(ROOT/"config/player_engine_profiles.json",baseline)
    atomic_bytes(ROOT/"player/runtime/majiro_engine_profiles.lua",(
        "-- Recovered original engine behavior; keyed by executable SHA-256.\nreturn "+
        lua_value(baseline["executables"])+"\n").encode("utf8"))
    lua=create_lua()
    platforms=table_value(lua.execute((ROOT/"player/runtime/majiro_platform_profiles.lua").read_text(encoding="utf8")))
    platforms.update(report["platform_executables"])
    atomic_bytes(ROOT/"player/runtime/majiro_platform_profiles.lua",(
        "-- Recovered original platform behavior; keyed by executable SHA-256.\nreturn "+
        lua_value(platforms)+"\n").encode("utf8"))
    return 0


if __name__=="__main__":raise SystemExit(main())
