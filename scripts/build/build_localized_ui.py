"""Build translated UI images once and link them into the formal resource view."""
from __future__ import annotations
import os
from pathlib import Path
import sys
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/"convert"))
from port_common import ROOT,read_json,write_json,sha256,atomic_bytes,lua_value
from majiro_image import Decoder
from ui_image_localization import identity


def run(verify_only=False):
    manifest=read_json(ROOT/"localization/ui_images.json")
    decoder=Decoder()
    catalogue_path=ROOT/"resources/image_catalogue.json"
    catalogue=read_json(catalogue_path)
    current=identity();records=[]
    for group in manifest["groups"]:
        names=[]
        for name in group["images"]:
            names.append(name)
            mask=name[:-4]+"_.rc8"
            if decoder.catalogue.key(mask) in decoder.catalogue.lookup:
                names.append(mask)
        for name in names:
            asset=catalogue.get(name)
            if not asset or asset.get("localization",{}).get("identity")!=current:
                if verify_only:raise ValueError("localized UI asset is stale: "+name)
                asset=decoder.convert(name);catalogue[name]=asset
            path=ROOT/"resources"/asset["file"]
            formal=ROOT/"localization/images"/path.name
            if not formal.exists()or not os.path.samefile(formal,path)or sha256(path)!=asset["sha256"]:
                raise ValueError("localized UI asset is no longer shared: "+name)
            records.append({"name":name,"file":str(formal.relative_to(ROOT)),
                "labels":asset["localization"]["labels"],"bytes":path.stat().st_size})
    if not verify_only:
        write_json(catalogue_path,catalogue)
        atomic_bytes(ROOT/"resources/scripts/image_catalogue.lua",("return "+lua_value(catalogue)+"\n").encode("utf-8"))
    report={"status":"passed","images":len(records),"labels":sum(x["labels"]for x in records),
        "identity":current,"files":records,"unique_png_bytes":sum(x["bytes"]for x in records),
        "runtime_resource_copies":0,"font":manifest["font"],"verify_only":verify_only}
    write_json(ROOT/"reports/ui_localization.json",report)
    print("localized UI:",len(records),"images,",report["labels"],"labels; resource copies: 0")
    return report


if __name__=="__main__":
    run("--verify-only"in sys.argv)
