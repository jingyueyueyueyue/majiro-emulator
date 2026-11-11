"""A zero CreateFont height selects the original default, without rejecting valid UI calls."""
from pathlib import Path
import sys
sys.path[:0]=[str(Path(__file__).resolve().parents[1]/"convert")]
from lua_bridge import create_lua
from native_pixels import Pixels
from port_common import ROOT,archive_dir,write_json,timestamp


def main():
    lua=create_lua();native=Pixels();providers=lua.table()
    state=lua.table_from({"frontbuffer":1,"registry":{},"pages":{1:{"width":256,"height":64}}},recursive=True)
    vm=lua.table_from({"thread_id":1,"threads":{1:{}}},recursive=True)
    define=lua.eval("function(p)return function(n,f)p[n]=f end end")(providers)
    requests=[]
    def graphics(owner,request):
        requests.append(request)
        response=native(request)
        if not response.startswith("OK"):raise RuntimeError(response)
        return response
    module=lua.execute((ROOT/"player/runtime/majiro_fontout.lua").read_text(encoding="utf8"))
    module["install"](define,state,lua.table_from({"font_face":"Source Han Sans SC"}),graphics)
    native("create 1 256 64 0")
    rows=[]
    for height in(0,16,20,24):
        providers["$fontout_font"](vm,lua.table_from(["MS Gothic",height,0]))
        providers["$fontout_locate"](vm,lua.table_from([1,4,4]))
        providers["$fontout"](vm,lua.table_from(["ABC"]))
        assert vm["threads"][1]["fontout"]["height"]==height
        raster=int(requests[-1].split("\n",1)[0].split()[4])
        assert raster==(20 if height==0 else height)
        rows.append({"source_height":height,"raster_height":raster,"status":"passed"})
    folder=archive_dir("fontout_zero_height")
    report={"status":"passed","timestamp":timestamp(),"cases":rows,
            "actual_native_raster":True,"source_font_state_retained":True,"device_accessed":False}
    write_json(folder/"report.json",report);write_json(ROOT/"reports/fontout_zero_height_tests.json",report)
    print("Fontout zero height:",len(rows),"actual pixel cases passed")


if __name__=="__main__":main()
