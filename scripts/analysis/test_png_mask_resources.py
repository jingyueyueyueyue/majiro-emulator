"""Check PNG-derived indexed mask aliases using the real reported resource and shared C++ code."""
from __future__ import annotations
import io
import json
import os
from pathlib import Path
import subprocess
import sys
from PIL import Image
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/"build"))
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/"convert"))
from build_player_core import msvc_environment
from majiro_arc import Archive
from port_common import ROOT,archive_dir,read_json,sha256,timestamp,write_json


def main():
    folder=archive_dir("png_mask_resources")
    root=Path(next(g["directory"]for g in read_json(ROOT/"config/player_samples.json")["games"]if g["id"]=="narci_onetwo"))
    archive=Archive(root/"fastdata.arc")
    source=next(m for m in archive.members if m.name=="ボタン説明.png")
    image=Image.open(io.BytesIO(source.read())).convert("RGBA")
    (folder/"rgba.bin").write_bytes(image.tobytes())
    env=msvc_environment()
    compiler=Path(env["VCTOOLSINSTALLDIR"])/"bin/Hostx64/x64/cl.exe"
    command=[str(compiler),"/nologo","/std:c++20","/EHsc","/O2","/utf-8","/I"+str(ROOT/"player/core"),
        str(ROOT/"scripts/analysis/png_mask_resource_probe.cpp"),str(ROOT/"player/core/majiro_image.cpp"),
        "/Fe:"+str(folder/"probe.exe")]
    result=subprocess.run(command,cwd=folder,env=env,capture_output=True)
    (folder/"compile.txt").write_bytes(result.stdout+result.stderr)
    if result.returncode:raise RuntimeError("PNG regression compile: "+str(folder/"compile.txt"))
    # Use relative arguments: Windows CRT argv decoding must not transform Japanese paths.
    result=subprocess.run([str(folder/"probe.exe"),"rgba.bin"],cwd=folder,capture_output=True)
    (folder/"run.txt").write_bytes(result.stdout+result.stderr)
    if result.returncode:raise RuntimeError("PNG regression failed: "+str(folder/"run.txt"))
    report=json.loads(result.stdout.decode("utf8"))
    report.update(timestamp=timestamp(),real_source=source.name,source_sha256=source.digest(),
        image_size=list(image.size),evidence=str(folder.relative_to(ROOT)),device_accessed=False,
        scope="shared C++ image resource resolution, 8-bit reverse alpha and real PNG alpha roundtrip")
    write_json(ROOT/"reports/png_mask_resources.json",report)
    print(json.dumps(report,ensure_ascii=False))
    return 0


if __name__=="__main__":raise SystemExit(main())
