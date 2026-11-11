"""Keep incompatible portable save indexes intact while allowing a fresh startup."""
from pathlib import Path
import hashlib
import json
import sys
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/"convert"))
from lua_bridge import create_lua
from port_common import ROOT,archive_dir,timestamp,write_json


def main():
    lua=create_lua();files={};checks=[]
    def host(request):
        if request.startswith("sha256\n"):return"OK "+hashlib.sha256(request[7:].encode()).hexdigest()
        op,_,rest=request.partition(" ");name,_,data=rest.partition("\n")
        if op=="save_read":return"OK data\n"+files[name]if name in files else"OK absent"
        if op=="save_exists":return"OK "+str(int(name in files))
        if op=="save_write":files[name]=data;return"OK"
        if op=="save_delete":files.pop(name,None);return"OK"
        raise AssertionError(op)
    codec=lua.execute((ROOT/"player/runtime/majiro_codec.lua").read_text(encoding="utf8"))
    module=lua.execute((ROOT/"player/runtime/majiro_store.lua").read_text(encoding="utf8"))
    def store(identity,isolate=True):
        return module["new"](codec,host,identity,lua.table(),lua.table_from({"partition_foreign":isolate}))
    a,b="a"*64,"b"*64
    first=store(a);index=first["load_index"](first);index["profile"]=lua.table_from({"marker":"old-progress"})
    first["commit"](first,index);original=dict(files)
    second=store(b);index2=second["load_index"](second)
    assert not list(index2["slots"])and index2["profile"]is None and second["foreign_identity"]==a
    assert files==original;checks.append("mismatch permits startup without importing old progress or modifying a byte")
    index2["profile"]=lua.table_from({"marker":"new-progress"});second["commit"](second,index2)
    assert all(files[k]==v for k,v in original.items());checks.append("new session keeps the original index and all old save objects")
    restored=store(b);assert restored["load_index"](restored)["profile"]["marker"]=="new-progress"
    previous=store(a);assert previous["load_index"](previous)["profile"]["marker"]=="old-progress"
    checks.append("each script set independently retains its own profile across a cold startup")
    strict=store("c"*64,False)
    try:strict["load_index"](strict)
    except Exception as error:assert"different script set"in str(error)
    else:raise AssertionError("strict non-Android store unexpectedly accepted foreign scripts")
    checks.append("strict callers retain the original identity rejection")
    corrupt=dict(files);files["index.mjs"]="broken"
    try:store(b)["load_index"](store(b))
    except Exception:pass
    else:raise AssertionError("invalid save data was silently accepted")
    files.clear();files.update(corrupt)
    checks.append("corrupt index data still fails its integrity/codec checks")
    folder=archive_dir("save_identity_partitions")
    report={"status":"passed","timestamp":timestamp(),"checks":checks,"device_accessed":False,
            "existing_player_saves_modified":False,"evidence":str(folder.relative_to(ROOT))}
    write_json(ROOT/"reports/save_identity_partition_tests.json",report);write_json(folder/"report.json",report)
    print(json.dumps(report,ensure_ascii=True))
    return 0


if __name__=="__main__":raise SystemExit(main())
