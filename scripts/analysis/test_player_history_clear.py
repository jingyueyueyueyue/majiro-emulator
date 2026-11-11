"""Independent console clear/eviction and saved history ownership regression."""
from pathlib import Path
import sys

from audit_player_semantics import registered_providers
from lua_bridge import table_value

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/"convert"))
from port_common import ROOT, archive_dir, timestamp, write_json


def run():
    lua,_,_,system=registered_providers(overrides={"graphics":lambda request:"OK"})
    state=system["state"];console=state["console"]
    console["history"]=lua.table_from([{"text":"old","bytes":39999}],recursive=True)
    console["history_bytes"]=39999
    system["providers"]["$histbuff_clear"](lua.table(),lua.table())
    assert len(console["history"])==0 and console["history_bytes"]==0
    console["configuration"]=lua.table_from([1,0,0,200,100])
    console["font_spec"]=lua.table_from(["MS Gothic",20,10,24,0])
    console["visible"]=True
    vm=lua.eval("""function()
      local frame={script={name='history-regression'},index=1}
      return{frames={frame},thread_id=1,current_offset=0,
        frame=function(self)return frame end,
        resume=function(self,action)self.last=action end}
    end""")()
    # History must retain both newly read entries after the old ring is
    # cleared; stale bytes previously evicted the first new record.
    for text in("first","second"):
        system["text"]["execute"](vm,"ctrl","w")
        system["text"]["execute"](vm,"text",text)
        system["text"]["execute"](vm,"proc",None)
        system["text"]["execute"](vm,"ctrl","p")
    assert [row["text"]for row in console["history"].values()]==["first","second"]
    assert console["history_bytes"]==11
    first=console["history"][1]["font_spec"][2]
    system["providers"]["$console_font"](vm,lua.table_from(["MS Gothic",32,16,40,0]))
    assert console["history"][1]["font_spec"][2]==first
    report={"status":"passed","timestamp":timestamp(),"cases":3,
            "scope":"clearing ring-byte accounting, retaining both post-clear entries, prior font ownership",
            "runtime":"player/runtime/majiro_console.lua and majiro_text.lua"}
    folder=archive_dir("history_clear")
    write_json(folder/"report.json",report)
    write_json(ROOT/"reports/player_history_clear_tests.json",report)
    print(report)
    return report


if __name__=="__main__":run()
