"""Targeted original old/new build regression after expanding new-engine fixtures."""
import hashlib
import json
from pathlib import Path
import sys
from recover_numeric_profiles import recover
from original_platform_controls import PlatformOracle
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/"convert"))
from port_common import ROOT,archive_dir,timestamp,write_json


def main():
    samples=json.loads((ROOT/"reports/opcode_dispatch.json").read_text(encoding="utf8"))["samples"]
    dispatch=json.loads((ROOT/"reports/syscall_dispatch.json").read_text(encoding="utf8"))["samples"]
    expected=json.loads((ROOT/"config/player_engine_profiles.json").read_text(encoding="utf8"))["executables"]
    evidence=archive_dir("new_profile_fixture_tests")
    rows=[]
    for name in("RURINOKASANE","narci_himeko"):
        sample=next(row for row in samples if row["sample"]==name)
        entries=next(row["entries"]for row in dispatch if row["exe_sha256"]==sample["exe_sha256"])
        actual=recover(sample,entries)
        profile=expected[sample["exe_sha256"]]
        for key,value in profile.items():
            if key=="numeric":assert actual["numeric_profile"]==value,(name,key)
            elif key in actual:assert actual[key]==value,(name,key,actual[key],value)
        platform=PlatformOracle(sample,entries).profile()
        rows.append({"sample":name,"exe_sha256":sample["exe_sha256"],"status":"passed",
                     "profile":actual,"platform_controls":platform})
        print(name,"expanded fixture keeps original profile",flush=True)
    report={"schema":1,"timestamp":timestamp(),"status":"passed","samples":rows,
            "device_accessed":False,"gameplay_started":False,"evidence":str(evidence.relative_to(ROOT)),
            "source_sha256":{name:hashlib.sha256((ROOT/"scripts/analysis"/name).read_bytes()).hexdigest()
                for name in("original_opcode.py","original_strings.py","original_calls.py","original_modes.py",
                            "original_text.py","original_audio.py","original_extsound.py","original_voice_playback.py",
                            "original_legacy_voice.py","original_screen.py","original_platform_controls.py",
                            "original_dialogs.py","recover_numeric_profiles.py")}}
    write_json(ROOT/"reports/new_profile_fixture_tests.json",report)
    write_json(evidence/"20261110_new_profile_fixture_tests.json",report)


if __name__=="__main__":main()
