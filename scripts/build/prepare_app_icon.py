"""Prepare the user-requested temporary application icon from their local game EXE."""
from __future__ import annotations
import io
from pathlib import Path
import struct
import sys
from PIL import Image
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/"analysis"))
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/"convert"))
from test_game_icons import pillow_icon
from port_common import ROOT,atomic_bytes,read_json,sha256,timestamp,write_json


def main():
    game=next(g for g in read_json(ROOT/"config/player_samples.json")["games"]if g["id"]=="Amber Quartz")
    root=Path(game["directory"])
    source=next(p for p in root.glob("*.exe")if p.name.startswith("アンバー"))
    width,height,digest,png,raw=pillow_icon(source)
    bits=32 if png else struct.unpack_from("<H",raw,14)[0]
    ico=struct.pack("<HHH",0,1,1)+struct.pack("<BBBBHHII",width if width<256 else 0,
        height if height<256 else 0,0,0,1,bits,len(raw),22)+raw
    image=Image.open(io.BytesIO(ico)).convert("RGBA")
    output=io.BytesIO();image.save(output,format="PNG")
    target=ROOT/"player/android/res/drawable/majiro_game_icon.png"
    atomic_bytes(target,output.getvalue())
    write_json(ROOT/"reports/app_icon.json",{"timestamp":timestamp(),"status":"prepared","temporary":True,
        "source":str(source),"source_sha256":sha256(source),"icon":str(target.relative_to(ROOT)),
        "sha256":sha256(target),"size":list(image.size),"original_game_modified":False,
        "basis":"user explicitly requested the local Amber Quartz icon"})
    print("App icon:",image.size,target)
    return 0


if __name__=="__main__":raise SystemExit(main())
