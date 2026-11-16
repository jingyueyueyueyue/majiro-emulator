"""Inventory Android save partitions without changing apps, files or permissions."""
from pathlib import Path,PurePosixPath
import argparse
import hashlib
import json
import shlex
import subprocess
import sys
import xml.etree.ElementTree as ET

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/"convert"))
from port_common import ROOT,archive_dir,timestamp,write_json

PACKAGES=("org.majiro.player","org.majiro.player.recovery",
          "com.tyranor.runtime.majiro","com.tyranor.runtime.majiro.recovery")


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--serial",default="ea615a82")
    args=parser.parse_args()
    folder=archive_dir("android_save_stores")
    base=["adb","-s",args.serial]
    def shell(*parts,required=True):
        result=subprocess.run([*base,"shell",shlex.join(parts)],capture_output=True,timeout=120)
        if result.returncode and required:raise RuntimeError(result.stderr.decode("utf8","replace"))
        return result
    installed=shell("pm","list","packages").stdout.decode("utf8").splitlines()
    rows=[]
    for package in PACKAGES:
        if "package:"+package not in installed:continue
        # Fixed package IDs and a fixed app-owned path; no shell-built user paths.
        result=shell("run-as",package,"sh","-c",
                     "find files/games -type f -print0 | xargs -0 -r -n 128 sha256sum",
                     required=False)
        if result.returncode and b"No such file"not in result.stderr:
            raise RuntimeError(result.stderr.decode("utf8","replace"))
        files={}
        for line in result.stdout.decode("utf8").splitlines():
            digest,path=line.split(None,1)
            assert len(digest)==64 and path.startswith("files/games/")
            files[path]=digest
        write_json(folder/(package+"_files.json"),files)
        roots=shell("run-as",package,"cat","shared_prefs/game-save-roots.xml",required=False)
        mappings={}
        if roots.returncode==0:
            mappings={item.attrib["name"]:item.text or""for item in ET.fromstring(roots.stdout)
                      if item.tag=="string"}
        indexes=[path for path in files if PurePosixPath(path).name.startswith("index")
                 and path.endswith(".mjs")]
        fingerprint=hashlib.sha256(json.dumps(files,sort_keys=True).encode()).hexdigest()
        rows.append({"package":package,"files":len(files),"indexes":len(indexes),
                     "store_roots":len({"/".join(path.split("/")[:3])for path in files}),
                     "inventory_sha256":fingerprint,"storage_mappings":len(mappings),
                     "inventory":str((folder/(package+"_files.json")).relative_to(ROOT))})
    report={"status":"passed","timestamp":timestamp(),"device_serial":args.serial,
            "packages":rows,"read_only":True,"player_files_modified":False,
            "evidence":str(folder.relative_to(ROOT)),
            "scope":"SHA-256 inventory of installed package save roots; format compatibility tested separately",
            "cross_package_note":"Different Android application IDs own separate private stores. Same-package APK updates preserve the existing store; runtime format tests do not migrate across application IDs."}
    write_json(folder/"report.json",report)
    write_json(ROOT/"reports/android_save_stores.json",report)
    print(json.dumps(report))
    return 0


if __name__=="__main__":raise SystemExit(main())
