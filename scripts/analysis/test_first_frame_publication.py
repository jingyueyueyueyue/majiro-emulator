"""Execute the production tick with a suspended first menu and real native pixels."""
from pathlib import Path
import sys
from PIL import Image
sys.path[:0]=[str(Path(__file__).resolve().parents[1]/"convert")]
from lua_bridge import create_lua
from native_pixels import Pixels
from port_common import ROOT,archive_dir,write_json,timestamp


def main():
    folder=archive_dir("first_frame_publication")
    source=(ROOT/"player/runtime/player_boot.lua").read_text(encoding="utf8")
    tick=source[source.index("function PLAYER_TICK("):source.index("\nfunction PLAYER_SHUTDOWN")]
    lua=create_lua();native=Pixels();calls=[]
    assert native("create 1 8 8 0")=="OK"
    assert native("fill 1 0 0 8 8 65280")=="OK"
    def graphics(request):
        calls.append(request)
        return native(request.replace("screen\n",str(folder/"frame.png")+"\n"))
    lua.globals()["HOST"]=lua.table_from({"graphics":graphics})
    state=lua.table_from({"frontbuffer":1,"invalidate":False,"invalidate_depth":1,"ui_waiting":True,
                         "settings":{},"sprite_order":{},"sprites":{},"present_requested":0},recursive=True)
    lua.globals()["STATE"]=state
    factory=lua.execute("""
      return function(tick_source)
        local prefix=[[
          local clock,last_font_quality,last_present_reply=0,nil,nil
          local submitted_frames,last_flush=0,0
          local online=nil
          local update_online=function()end
          local vm={status='waiting',steps=0,run=function()end,frame=function()end}
          local system={state=STATE,poll=function()end,
            dialogs={active=function()return false end},sprites={update=function()end},
            screen={present=function()return 'presentview 1 0 0 0 screen\\n'end},
            presented=function()end}
        ]]
        return assert(loadstring(prefix..tick_source..
          '\\nreturn function(t,event)PLAYER_TICK(t,event);return submitted_frames,last_present_reply end'))()
      end
    """)
    run=factory(tick)
    assert run(0,lua.table())[0]==1 and len(calls)==1
    assert state["invalidate_depth"]==1 and state["invalidate"]==False
    assert run(16,lua.table())[0]==1 and len(calls)==1
    state["invalidate"]=True;state["invalidate_depth"]=0
    assert run(32,lua.table())[0]==2 and len(calls)==2
    with Image.open(folder/"frame.png")as frame:
        assert frame.convert("RGB").getpixel((4,4))==(0,255,0)
    lua.globals()["HOST"]=lua.table_from({"graphics":lambda request:"ERR unknown page handle"})
    failing=factory(tick)
    failed=False
    try:failing(0,lua.table())
    except Exception as error:
        assert "frame composition failed"in str(error);failed=True
    assert failed,"native composition error was silently discarded"
    report={"status":"passed","timestamp":timestamp(),
            "cases":["first suspended input scene produces a nonblack native frame",
                     "nested draw counter stays unchanged","later suspended ticks preserve the first frame",
                     "normal draw resumes after enable","composition ERR is surfaced"],
            "actual_native_pixels":True,"device_accessed":False}
    write_json(folder/"report.json",report);write_json(ROOT/"reports/first_frame_publication_tests.json",report)
    print("First frame publication:",len(report["cases"]),"production checks passed")


if __name__=="__main__":main()
