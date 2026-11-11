"""Compare sprite ordering with the original SWITCH x86 linked-list handlers."""
from pathlib import Path
import hashlib
import json
import random
import sys
import capstone

sys.path[:0]=[str(Path(__file__).resolve().parents[1]/"convert")]
from lua_bridge import create_lua
from test_new_graphics_abi import FunctionFixture
from port_common import ROOT,archive_dir,write_json,timestamp


def main():
    sample=next(row for row in json.loads((ROOT/"reports/opcode_dispatch.json").read_text(encoding="utf8"))["samples"]
                if row["sample"]=="SWITCH")
    dispatch=next(row for row in json.loads((ROOT/"reports/syscall_dispatch.json").read_text(encoding="utf8"))["samples"]
                  if row["exe_sha256"]==sample["exe_sha256"])
    executable=Path(sample["path"])
    assert hashlib.sha256(executable.read_bytes()).hexdigest()==sample["exe_sha256"]
    native=FunctionFixture(sample)
    native.machine=native.uc
    from native_function import NativeFunctions
    functions=NativeFunctions(native)
    helpers={e["name"]:functions.syscall_helper(e)for e in dispatch["entries"]
             if e.get("name")and e["name"].startswith("$sprite_priority_")}
    native.hook(0x41dae0,lambda sp:native.top)
    native.hook(0x41db00,lambda sp:0)
    native.hook(0x43c5a0,lambda sp:0)
    head,tail=0x5720a0,0x5720a4
    previous,following,group=0x600,0x604,0x30
    lua=create_lua();providers=lua.table();state=lua.table()
    define=lua.eval("function(p)return function(n,f)p[n]=f end end")(providers)
    options=lua.table_from({"width":800,"height":600,"now":lambda:0})
    module=lua.execute((ROOT/"player/runtime/majiro_sprites.lua").read_text(encoding="utf8"))
    module["install"](define,state,options,lambda *args:"OK",providers)
    state["pages"]=lua.table_from({10:{"width":8,"height":8},20:{"width":800,"height":600}},recursive=True)
    state["frontbuffer"]=20
    vm=lua.table();vm["frame"]=lambda self:None
    handles=[providers["$sprite_create"](vm,lua.table_from([10]))for _ in range(8)]
    addresses={handle:0x10050000+i*0x800 for i,handle in enumerate(handles)}
    reverse={value:key for key,value in addresses.items()}
    # Native next links traverse high -> low; the compositor table is low -> high.
    order=list(reversed(handles))
    for i,handle in enumerate(order):
        at=addresses[handle]
        native.put(at+previous,addresses[order[i-1]]if i else 0)
        native.put(at+following,addresses[order[i+1]]if i+1<len(order)else 0)
        native.put(at+group,int(handle))
    native.put(head,addresses[order[0]]);native.put(tail,addresses[order[-1]])
    cases=[]
    def compare(name,args):
        native.call(helpers[name],[addresses[a]for a in args])
        providers[name](vm,lua.table_from(args))
        linked=[];pointer=native.read(head)
        while pointer:
            assert pointer not in linked,"native linked-list cycle"
            linked.append(pointer);pointer=native.read(pointer+following)
        wanted=[reverse[p]for p in reversed(linked)]
        actual=list(state["sprite_order"].values())
        assert actual==wanted,(name,args,actual,wanted)
        for handle in handles:
            assert state["sprites"][handle]["priority_group"]==native.read(addresses[handle]+group)
        cases.append({"provider":name,"argc":len(args),"status":"passed"})
    # Repeated "above this background" must preserve prior siblings' order.
    for handle in handles[1:6]:
        compare("$sprite_priority_high",[handle,handles[0]])
    compare("$sprite_priority_high_single",[handles[7],handles[1]])
    compare("$sprite_priority_low",[handles[6],handles[2]])
    compare("$sprite_priority_low_single",[handles[7],handles[3]])
    rng=random.Random(171027)
    names=list(helpers)
    for _ in range(300):
        first,second=rng.sample(handles,2)
        compare(rng.choice(names),[first,second]if rng.randrange(4)else[first])
    folder=archive_dir("sprite_priority_groups")
    report={"status":"passed","timestamp":timestamp(),"comparisons":len(cases),"cases":cases,
            "exe_sha256":sample["exe_sha256"],"original_handlers":helpers,
            "original_code_executed":True,"device_accessed":False,
            "source_sha256":hashlib.sha256((ROOT/"player/runtime/majiro_sprites.lua").read_bytes()).hexdigest()}
    write_json(folder/"report.json",report);write_json(ROOT/"reports/sprite_priority_groups_tests.json",report)
    print("Sprite priority groups:",len(cases),"original x86 comparisons passed")


if __name__=="__main__":main()
