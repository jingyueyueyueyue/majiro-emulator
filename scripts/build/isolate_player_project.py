"""Create independent player source copies; never hardlink to the old port."""
from __future__ import annotations

from pathlib import Path
import sys

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/"convert"))
from port_common import ROOT,archive_dir,atomic_bytes,sha256,timestamp,write_json


def main():
    sources=[(path,ROOT/"player/runtime"/path.name)for path in(ROOT/"runtime").glob("*.lua")
             if not path.name.startswith("artemis_")]
    sources.extend((ROOT/"resources/scripts"/name,ROOT/"player/tables"/name)
                   for name in("opcodes.lua","syscall_names.lua"))
    sources.append((ROOT/"runtime/native/majiro_pixels.cpp",ROOT/"player/native/majiro_pixels.cpp"))
    rows=[]
    for source,target in sources:
        raw=source.read_bytes()
        if target.exists()and target.read_bytes()!=raw:
            raise RuntimeError("independent player already diverged; refusing resynchronization: "+str(target))
        atomic_bytes(target,raw)
        if source.stat().st_ino==target.stat().st_ino:
            raise RuntimeError("independent player must not share source hardlinks")
        rows.append({"reference_source":source.relative_to(ROOT).as_posix(),
                     "independent_source":target.relative_to(ROOT).as_posix(),"sha256":sha256(target)})
    evidence=archive_dir("player_project_isolation")
    report={"timestamp":timestamp(),"status":"copied_independent_sources","files":rows,
        "hardlinks":False,"future_automatic_sync":False,"old_port_mutated":False,
        "player_root":"player","old_port_runtime":"runtime",
        "shared_analysis_and_original_inputs":True}
    write_json(evidence/(evidence.name[:8]+"_isolation.json"),report)
    write_json(ROOT/"reports/player_project_isolation.json",report)
    print("Independent player source copies:",len(rows))


if __name__=="__main__":main()
