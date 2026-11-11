"""Probe the initialized Happy Princess reference while retaining original identities."""
from __future__ import annotations
import copy
from pathlib import Path
import sys
import traceback
from recover_numeric_profiles import recover
from original_platform_controls import PlatformOracle
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/"convert"))
from port_common import ROOT,archive_dir,read_json,write_json,timestamp


def main():
    scan=read_json(ROOT/"reports/new_sample_scan.json")
    wrapper=read_json(ROOT/"reports/happy_wrapper_recovery.json")
    sample=copy.deepcopy(next(s for s in scan["opcode_samples"]if s["sample"]=="happy_princess"))
    entries=next(s["entries"]for s in scan["syscall_samples"]if s["sample"]=="happy_princess")
    sample.update(path=wrapper["initialized_reference"],exe_sha256=wrapper["initialized_reference_sha256"])
    folder=archive_dir("happy_profile")
    try:
        row=recover(sample,entries)
        row["platform_controls"]=PlatformOracle(sample,entries).profile()
        row.update(status="passed",timestamp=timestamp(),aliases=[wrapper["raw_embedded_sha256"],wrapper["container_sha256"]],
                   reference_path=wrapper["initialized_reference"],evidence=str(folder.relative_to(ROOT)),
                   windows_game_started=False,device_accessed=False)
        write_json(ROOT/"reports/happy_engine_profile.json",row)
        write_json(folder/"profile.json",row)
        print("Happy original profile passed:",row["numeric_profile"],row["screen_controls"])
    except Exception:
        detail=traceback.format_exc();(folder/"failure.txt").write_text(detail,encoding="utf8")
        print(detail);return 1
    return 0


if __name__=="__main__":raise SystemExit(main())
