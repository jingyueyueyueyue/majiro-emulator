"""Pin translation members in the existing ZIP; do not extract or execute its binaries."""
from __future__ import annotations
import hashlib
from pathlib import Path
import sys
import zipfile
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/"convert"))
from port_common import ROOT,read_json,write_json,sha256


def configure(path):
    path=Path(path).resolve(strict=True)
    records=[];skipped=[]
    with zipfile.ZipFile(path)as z:
        for info in z.infolist():
            if info.is_dir():continue
            name=Path(info.filename).name
            if "/scenario/"in info.filename and Path(name).suffix.lower()in(".mjo",".cfg",".bin"):
                records.append({"entry":info.filename,"name":name,"bytes":info.file_size,
                                "sha256":hashlib.sha256(z.read(info)).hexdigest()})
            else:
                row={"entry":info.filename,"bytes":info.file_size,"used":False}
                if name=="fastdata.arc":
                    row["sha256"]=hashlib.sha256(z.read(info)).hexdigest()
                    if row["sha256"]!=sha256(ROOT/name):
                        raise ValueError("patch contains a changed ARC; explicit overlay support required")
                    row["reason"]="byte-identical to existing original, reused in place"
                else:row["reason"]="not a Majiro script/config overlay; not needed by Artemis"
                skipped.append(row)
        uif=json_from_zip(z,next(i for i in z.namelist()if i.endswith("uif_config.json")))
        substitution=uif["character_substitution"]
        source,target=substitution["source_characters"],substitution["target_characters"]
        if len(source)!=len(target):raise ValueError("patch character map length mismatch")
        charmap=dict(zip(source,target))
        tunnel=z.read(next(i for i in z.namelist()if i.endswith("sjis_ext.bin"))).decode("utf-16le")
    record={"schema":1,"zip":str(path),"sha256":sha256(path),"zip_bytes":path.stat().st_size,
        "overrides":records,"skipped":skipped,"tunnel_characters":tunnel,
        "encoding_reference":{"repository":"arcusmaximus/VNTranslationTools",
            "commit":"36fbe909d02f2e9bb187443e03e2df595f56bfec",
            "file":"VNTextPatch.Shared/Util/SjisTunnelEncoding.cs"},
        "policy":"translation ZIP overrides original member names; original ARC and EXE remain unchanged"}
    write_json(ROOT/"localization/patch.lock.json",record)
    write_json(ROOT/"localization/character_map.json",charmap)
    print("translation ZIP pinned:",len(records),"members; extracted bytes: 0")
    return record


def json_from_zip(z,name):
    import json
    return json.loads(z.read(name).decode("utf-8-sig"))


if __name__=="__main__":
    import argparse
    parser=argparse.ArgumentParser();parser.add_argument("zip")
    configure(parser.parse_args().zip)
