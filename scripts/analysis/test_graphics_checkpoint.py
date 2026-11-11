"""Independent dead-scene pruning fixtures with mask dependencies and exact pixels."""
from __future__ import annotations
from pathlib import Path
import sys
from lua_bridge import create_lua
from native_pixels import Pixels
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/"convert"))
from port_common import ROOT,write_json


def run():
    lua=create_lua()
    compact=lua.execute((ROOT/"runtime/majiro_graphics_checkpoint.lua").read_text(encoding="utf8"))["compact"]
    native=Pixels()
    cases=[]
    fixtures=[
        (["create 1 4 4 0","fill 1 0 0 4 4 65535","create 2 4 4 0",
          "fill 2 0 0 4 4 255","copy 2 0 0 4 4 1 0 0","release 2",
          "create 3 4 4 0","fill 3 0 0 4 4 0","release 3"],{1:32}),
        (["create 1 4 4 0","fill 1 0 0 4 4 65535","create 2 4 4 0",
          "fill 2 0 0 4 4 255","copy 2 -1 0 4 4 1 0 0","release 2"],{1:32}),
        (["create 1 4 4 0","alpha 1 2 0","fill 1 0 0 4 4 65280",
          "fill 2 0 0 4 4 127","create 3 4 4 0 8","fill 3 0 0 4 4 13",
          "antidata 1 3","create 4 4 4 0","fill 4 0 0 4 4 255",
          "paste 4 1 0 0 4 4 0 0 1 1 0 0 0"],{1:32,3:8,4:32}),
        (["create 1 4 4 0","fill 1 0 0 4 4 65535","create 2 4 4 0",
          "fill 2 0 0 4 4 255","drawmode 1 3","paste 2 1 0 0 4 4 0 0 1 1 0 64 0",
          "thumbnail 2 1"],{1:32,2:32}),
    ]
    for commands,pages in fixtures:
        live=lua.table_from({i:{"width":4,"height":4,"depth":depth}
                             for i,depth in pages.items()},recursive=True)
        pruned=list(compact(lua.table_from(commands),live).values())
        def fingerprint(sequence):
            assert native("reset")=="OK"
            for command in sequence:assert native(command)=="OK",command
            return {i:[native(f"pixel {i} {x} {y}")for y in range(4)for x in range(4)]for i in pages}
        assert fingerprint(commands)==fingerprint(pruned),(commands,pruned)
        cases.append({"original_commands":len(commands),"saved_commands":len(pruned)})
    report={"status":"passed","cases":cases,
            "scope":"overwrite/clipping, dead pages, alpha replacement, draw mode, thumbnail pixel equivalence"}
    write_json(ROOT/"reports/graphics_checkpoint_tests.json",report)
    print(report)
    return report


if __name__=="__main__":
    run()
