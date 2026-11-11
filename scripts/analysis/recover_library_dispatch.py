"""Rebuild the comparison evidence for all restored games in parallel."""
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor,as_completed
import json
import sys
sys.path[:0]=[str(Path(__file__).resolve().parents[1]/"convert")]
from scan_majiro_samples import scan
from compare_majiro_samples import reference_exe,compare
from recover_majiro_dispatch import recover as opcodes
from recover_majiro_syscalls import recover as syscalls
from majiro_mjo import opcode_table
from port_common import ROOT,archive_dir,read_json,write_json,timestamp


def main():
    games=read_json(ROOT/"config/recovered_library.json")["games"]
    definitions=opcode_table()
    names=read_json(ROOT/"references/majiro-py/known_hashes.json")["syscalls"]
    folder=archive_dir("recovered_dispatch")
    rows=[]
    def task(game):
        record={"id":game["id"],"error":None}
        try:
            source=scan(Path(game["directory"]),definitions,True)
            source["id"]=game["id"];record["game"]=source
            executable=reference_exe(source)
            record["opcode"]=opcodes(executable["path"],definitions)
            record["opcode"]["sample"]=game["id"]
            record["syscall"]=syscalls(source,names)
            record["syscall"]["sample"]=game["id"]
        except Exception as error:record["error"]=str(error)
        write_json(folder/(game["id"].replace(" ","_")+".json"),record)
        return record
    with ThreadPoolExecutor(max_workers=4)as pool:
        for future in as_completed([pool.submit(task,game)for game in games]):
            row=future.result();rows.append(row)
            print(row["id"],"opcodes",row.get("opcode",{}).get("active_opcode_count"),
                  "syscalls",row.get("syscall",{}).get("recovered_entries"),"error",row["error"],flush=True)
    write_json(ROOT/"reports/majiro_samples.json",{"schema":1,"timestamp":timestamp(),
        "games":[row["game"]for row in rows if "game"in row]})
    write_json(ROOT/"reports/opcode_dispatch.json",{"schema":1,"timestamp":timestamp(),
        "samples":[row["opcode"]for row in rows if "opcode"in row]})
    write_json(ROOT/"reports/syscall_dispatch.json",{"schema":1,"timestamp":timestamp(),
        "samples":[row["syscall"]for row in rows if "syscall"in row]})
    new={g["id"]for g in read_json(ROOT/"config/player_new_samples.json")["games"]}
    extra={"schema":1,"timestamp":timestamp(),"games":[r["game"]for r in rows if r["id"]in new and"game"in r],
           "opcode_samples":[r["opcode"]for r in rows if r["id"]in new and"opcode"in r],
           "syscall_samples":[r["syscall"]for r in rows if r["id"]in new and"syscall"in r],
           "call_sites":{},"errors":[{"game":r["id"],"error":r["error"]}for r in rows if r["error"]]}
    write_json(ROOT/"reports/new_sample_scan.json",extra)
    report={"status":"passed"if all(not r["error"]for r in rows)else"incomplete",
            "games":len(rows),"errors":[{"game":r["id"],"error":r["error"]}for r in rows if r["error"]],
            "sources_modified":False,"timestamp":timestamp(),"evidence":str(folder)}
    write_json(ROOT/"reports/recovered_dispatch.json",report)
    return int(bool(report["errors"]))


if __name__=="__main__":raise SystemExit(main())
