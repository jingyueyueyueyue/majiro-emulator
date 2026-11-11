"""Maintain the selected ADB connection while this task is being worked on."""
from __future__ import annotations

import argparse
import ctypes
import os
from pathlib import Path
import subprocess
import sys
import time

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/"convert"))
from port_common import ROOT,archive_dir,read_json,timestamp,write_json


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--serial",required=True)
    args=parser.parse_args()
    adb=Path(read_json(ROOT/"reports/android_toolchain.json")["sdk"])/"platform-tools/adb.exe"
    evidence=archive_dir("android_connection")
    stop=evidence/(evidence.name[:8]+"_stop")
    receipt=ROOT/"reports/android_connection.json"
    previous=None;last_heartbeat=0
    if os.name=="nt":
        # Task-scoped request; closing this process restores normal sleep policy.
        ctypes.windll.kernel32.SetThreadExecutionState(0x80000001)
    def command(arguments):
        try:
            result=subprocess.run([str(adb),*arguments],capture_output=True,timeout=15)
            return result.returncode,(result.stdout+result.stderr).decode("utf8","replace").strip()
        except subprocess.TimeoutExpired:return 1,"ADB request timed out"
    try:
        while not stop.exists():
            code,value=command(["-s",args.serial,"get-state"])
            state="connected"if code==0 and value=="device"else"unauthorized"if"unauthorized"in value else"disconnected"
            now=time.monotonic()
            if state!=previous or now-last_heartbeat>=600:
                row={"timestamp":timestamp(),"serial":args.serial,"state":state,"response":value,
                    "pid":os.getpid(),"stop_file":str(stop.relative_to(ROOT)),"evidence":str(evidence.relative_to(ROOT)),
                    "device_changes":False,"game_or_apk_started":False}
                write_json(receipt,row)
                with(evidence/(evidence.name[:8]+"_connection.jsonl")).open("a",encoding="utf8")as log:
                    import json
                    log.write(json.dumps(row,ensure_ascii=True)+"\n")
                print(row["timestamp"],state,flush=True);last_heartbeat=now
            if state=="disconnected"and previous=="disconnected":
                command(["start-server"])
                command(["-s",args.serial,"reconnect","offline"])
            previous=state
            for _ in range(20):
                if stop.exists():break
                time.sleep(1)
    finally:
        if os.name=="nt":ctypes.windll.kernel32.SetThreadExecutionState(0x80000000)
    print("ADB connection maintenance stopped",flush=True)


if __name__=="__main__":main()
