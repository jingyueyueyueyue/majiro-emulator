"""Independent player inputs; only analysis/original reference files are shared."""
from pathlib import Path
import sys

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/"convert"))
from port_common import ROOT

PLAYER=ROOT/"player"


def player_path(name):
    name=str(name).replace("\\","/")
    if name.startswith("runtime/"):return PLAYER/name
    if name=="scripts/tile_ai.lua":return PLAYER/"tables/tile_ai.lua"
    if name in("resources/scripts/opcodes.lua","resources/scripts/syscall_names.lua"):
        return PLAYER/"tables"/Path(name).name
    return ROOT/name
