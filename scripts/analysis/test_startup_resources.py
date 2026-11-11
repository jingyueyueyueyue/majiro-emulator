"""Require all configured games to reach their first source input boundary in all text modes."""
from pathlib import Path
import json
import subprocess
import sys
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/"convert"))
from port_common import ROOT,archive_dir,read_json,timestamp,write_json


def main():
    folder=archive_dir("startup_resource_regressions")
    with(folder/"audit.txt").open("wb")as log:
        result=subprocess.run([sys.executable,"-B","-X","utf8",
            str(ROOT/"scripts/analysis/audit_startup_resources.py"),"--seconds","35"],
            cwd=ROOT,stdout=log,stderr=subprocess.STDOUT,timeout=1200)
    report=read_json(ROOT/"reports/startup_resource_audit.json")
    games=read_json(ROOT/"config/player_samples.json")["games"]+read_json(ROOT/"config/player_new_samples.json")["games"]
    expected={(g["id"],mode)for g in games if g.get("enabled",True)for mode in("original","local","online")}
    seen={(row["game"],row["mode"])for row in report["games"]}
    failures=[row for row in report["games"]if row.get("error")or row.get("source_changed_during_run")or row["stop"]not in(
        "source_ui_wait","first_text_wait","input_boundary","dialog_boundary","authentication_boundary")]
    summary={"status":"passed"if result.returncode==0 and seen==expected and not failures else"failed",
             "timestamp":timestamp(),"cases":len(seen),"failures":failures,"missing":sorted(expected-seen),
             "decoded_page_images":sum(row.get("decoded_page_images",0)for row in report["games"]),
             "device_accessed":False,"actual_gameplay_verified":False,
             "scope":"source VM and actual pixel decoding until the first input boundary; online uses a local delayed mock",
             "evidence":str(folder.relative_to(ROOT)),"audit_evidence":report["evidence"]}
    write_json(ROOT/"reports/startup_resource_tests.json",summary);write_json(folder/"report.json",summary)
    print(json.dumps(summary,ensure_ascii=True))
    return 0 if summary["status"]=="passed"else 1


if __name__=="__main__":raise SystemExit(main())
