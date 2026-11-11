"""Read-only checker discovery in registered games and adjacent install media."""
from __future__ import annotations
import hashlib
import json
import os
from pathlib import Path
import sys

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/"convert"))
from majiro_arc import Archive
from port_common import ROOT,archive_dir,timestamp,write_json


def run():
    samples=json.loads((ROOT/"config/player_samples.json").read_text(encoding="utf8"))["games"]
    records=[];archives=[];candidates=[];seen=set()
    for sample in samples:
        root=Path(sample["directory"]);row={"sample":sample["id"],"game":str(root),"checked_dirs":[],
                                            "checker_paths":[],"adjacent_files":[],"arc_matches":[]}
        dirs=[root,*[p for p in root.iterdir()if p.is_dir()and p.name.lower()in
                    ("bin","system","tools","patch","update","extras")],root.parent,root.parent.parent]
        for directory in dirs:
            if directory==root.parent.parent and directory.name=="common":continue
            row["checked_dirs"].append(str(directory))
            try:
                for path in directory.iterdir():
                    if not path.is_file():continue
                    name=path.name.lower()
                    if "checker"in name:
                        row["checker_paths"].append(str(path));candidates.append(path)
                    if directory!=root and path.suffix.lower()in(
                        ".iso",".mdf",".mds",".exe",".zip",".7z",".rar",".cab",".msi",".bin"):
                        row["adjacent_files"].append({"path":str(path),"bytes":path.stat().st_size})
            except OSError as error:row.setdefault("errors",[]).append(str(error))
        for path in root.glob("*.arc"):
            if path.name.lower()=="savedata.arc":continue
            try:
                arc=Archive(path)
                for member in arc.members:
                    if "checker"in member.name.lower()or member.name.lower().endswith((".exe",".dll")):
                        row["arc_matches"].append({"archive":path.name,"name":member.name,"bytes":member.size})
            except Exception as error:row.setdefault("arc_errors",[]).append(str(error))
        records.append(row)
        print(sample["id"],"checker",len(row["checker_paths"]),"adjacent",len(row["adjacent_files"]),
              "archive executable candidates",len(row["arc_matches"]),flush=True)
    report={"schema":1,"timestamp":timestamp(),"samples":records,
            "scope":"game root and recognized direct tool dirs, parent and enclosing installer directory; ARC indexes only",
            "checker_candidates":list(dict.fromkeys(str(path)for path in candidates))}
    folder=archive_dir("checker_sources")
    write_json(folder/(folder.name[:8]+"_checker_sources.json"),report)
    write_json(ROOT/"reports/checker_sources.json",report)
    return report


if __name__=="__main__":run()
