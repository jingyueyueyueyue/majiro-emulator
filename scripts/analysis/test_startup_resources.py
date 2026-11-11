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
    retries=[]
    # Initial image decoding can exceed the short budget when independent
    # native comparisons run concurrently. Retry only genuine time budgets;
    # execution errors and source changes are never converted into success.
    for index,row in enumerate(report["games"]):
        if row.get("stop")!="budget"or row.get("error")or row.get("source_changed_during_run"):
            continue
        log=folder/("retry_"+str(index)+".txt")
        with log.open("wb")as output:
            retry=subprocess.run([sys.executable,"-B","-X","utf8",
                str(ROOT/"scripts/analysis/audit_startup_resources.py"),
                "--game",row["game"],"--mode",row["mode"],"--seconds","105"],
                cwd=ROOT,stdout=output,stderr=subprocess.STDOUT,timeout=300)
        observed=read_json(ROOT/"reports/startup_resource_audit.json")
        candidates=observed["games"]
        assert len(candidates)==1 and candidates[0]["game"]==row["game"]and candidates[0]["mode"]==row["mode"]
        retries.append({"game":row["game"],"mode":row["mode"],"before":row["stop"],
                        "after":candidates[0]["stop"],"evidence":observed["evidence"],
                        "returncode":retry.returncode})
        if retry.returncode==0:report["games"][index]=candidates[0]
    if retries:
        report["budget_retries"]=retries
        write_json(ROOT/"reports/startup_resource_audit.json",report)
    games=read_json(ROOT/"config/player_samples.json")["games"]+read_json(ROOT/"config/player_new_samples.json")["games"]
    expected={(g["id"],mode)for g in games if g.get("enabled",True)for mode in("original","local","online")}
    seen={(row["game"],row["mode"])for row in report["games"]}
    failures=[row for row in report["games"]if row.get("error")or not row.get("text_preflight_sealed")or
              not row.get("text_identity")or row.get("source_changed_during_run")or row["stop"]not in(
        "source_ui_wait","first_text_wait","input_boundary","dialog_boundary","authentication_boundary")]
    summary={"status":"passed"if result.returncode==0 and seen==expected and not failures else"failed",
             "timestamp":timestamp(),"cases":len(seen),"failures":failures,"missing":sorted(expected-seen),
             "decoded_page_images":sum(row.get("decoded_page_images",0)for row in report["games"]),
             "device_accessed":False,"actual_gameplay_verified":False,
             "scope":"source VM and actual pixel decoding until the first input boundary; online uses a local delayed mock",
             "local_unavailable_auto_fallbacks":sum(bool(row.get("local_unavailable_auto_fallback"))for row in report["games"]),
             "mode_policy":"production Java plans; local preference without a text patch uses original scripts",
             "evidence":str(folder.relative_to(ROOT)),"audit_evidence":report["evidence"]}
    summary["budget_retries"]=retries
    write_json(ROOT/"reports/startup_resource_tests.json",summary);write_json(folder/"report.json",summary)
    print(json.dumps(summary,ensure_ascii=True))
    return 0 if summary["status"]=="passed"else 1


if __name__=="__main__":raise SystemExit(main())
