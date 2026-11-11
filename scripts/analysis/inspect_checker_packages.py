"""List adjacent install packages and extract only an explicitly named checker."""
from __future__ import annotations
import hashlib
import json
from pathlib import Path
import subprocess
import sys

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/"convert"))
from port_common import ROOT,archive_dir,timestamp,write_json


def run():
    scan=json.loads((ROOT/"reports/checker_sources.json").read_text(encoding="utf8"))
    paths=sorted({row["path"]for sample in scan["samples"]for row in sample["adjacent_files"]
                  if Path(row["path"]).suffix.lower()in(".iso",".zip",".7z",".rar",".cab",".msi")})
    seven=Path("C:/Program Files/7-Zip/7z.exe")
    folder=archive_dir("checker_packages");rows=[]
    for number,text in enumerate(paths):
        path=Path(text)
        result=subprocess.run([str(seven),"l","-slt","-sccUTF-8",str(path)],capture_output=True,timeout=60)
        output=result.stdout.decode("utf8","replace").replace("\r","")
        (folder/(folder.name[:8]+f"_{number:02d}_listing.txt")).write_text(output,encoding="utf8")
        records=[]
        for section in output.split("\n\n"):
            values=dict(line.split(" = ",1)for line in section.splitlines()if" = "in line)
            if "Path"in values and values.get("Folder")=="-"and values.get("Size","").isdigit():
                records.append(values)
        matches=[record for record in records if "checker"in record["Path"].lower()]
        exes=[record for record in records if record["Path"].lower().endswith(
            (".exe",".dll",".cab",".msi",".bin",".dat",".arc"))]
        row={"path":str(path),"bytes":path.stat().st_size,"returncode":result.returncode,
             "members":len(records),"checker_matches":matches,"executable_or_payload_candidates":exes}
        for record in matches:
            if not record["Path"].lower().endswith(".exe"):continue
            if int(record["Size"])>32*1024*1024:raise ValueError("checker candidate exceeds bound")
            found=subprocess.run([str(seven),"x","-so","-sccUTF-8",str(path),record["Path"]],
                capture_output=True,timeout=60)
            if found.returncode or len(found.stdout)!=int(record["Size"]):raise ValueError("checker extraction failed")
            output_path=folder/(folder.name[:8]+f"_{number:02d}_checker.exe")
            output_path.write_bytes(found.stdout)
            row.setdefault("extracted",[]).append({"path":str(output_path),
                "sha256":hashlib.sha256(found.stdout).hexdigest(),"member":record["Path"]})
        rows.append(row)
        print(path.name,len(records),"members,",len(matches),"checker matches",flush=True)
    report={"schema":1,"timestamp":timestamp(),"packages":rows,
            "extracted_game_copies":False,"evidence":str(folder.relative_to(ROOT))}
    write_json(folder/(folder.name[:8]+"_checker_packages.json"),report)
    write_json(ROOT/"reports/checker_packages.json",report)
    return report


if __name__=="__main__":run()
