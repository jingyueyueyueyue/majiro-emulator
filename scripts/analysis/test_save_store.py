"""Fault injection for save publication, shared chunks, integrity, and collection."""
from __future__ import annotations
import hashlib
from pathlib import Path
import sys
from lua_bridge import create_lua,table_value
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/"convert"))
from port_common import ROOT,write_json


def run():
    lua=create_lua()
    files={}
    failure={"index":False}
    def host(request):
        if request.startswith("sha256\n"):
            return "OK "+hashlib.sha256(request[7:].encode("utf8")).hexdigest()
        operation,_,rest=request.partition(" ")
        name,_,data=rest.partition("\n")
        if operation=="save_exists":return "OK "+str(int(name in files))
        if operation=="save_read":return "OK data\n"+files[name]if name in files else"OK absent"
        if operation=="save_write":
            if failure["index"]and name=="index.mjs":return "ERR simulated power loss"
            files[name]=data;return "OK"
        if operation=="save_delete":files.pop(name,None);return "OK"
        raise ValueError(operation)
    codec=lua.execute((ROOT/"runtime/majiro_codec.lua").read_text(encoding="utf8"))
    module=lua.execute((ROOT/"runtime/majiro_store.lua").read_text(encoding="utf8"))
    lua.globals()["store"]=module["new"](codec,host,"a"*64)
    lua.globals()["STORE_MODULE"]=module
    lua.globals()["store_host"]=host
    lua.globals()["codec"]=codec
    lua.globals()["fail_index"]=lambda value:failure.update(index=value)
    lua.globals()["file_count"]=lambda:len(files)
    lua.globals()["corrupt"]=lambda name:files.update({name:"corrupted"})
    result=table_value(lua.execute(r'''
      local a=string.rep('same-prefix-',5000)..'A'
      local b=string.rep('same-prefix-',5000)..'B'
      local one,two=store:put(a),store:put(b)
      assert(one.parts[1]==two.parts[1] and one.parts[#one.parts]~=two.parts[#two.parts])
      assert(store:object_key(one)~=store:object_key(two))
      local count=file_count()
      store:put(a)
      assert(file_count()==count and store:get(one)==a and store:get(two)==b)
      local index={schema=1,identity=store.identity,slots={
        one={snapshot=one,graphics={}},two={snapshot=two,graphics={}}}}
      store:commit(index)
      local old=index.slots.one
      index.slots.one=nil
      store:commit(index);store:collect(old,index)
      assert(store:get(two)==b)
      assert(not store.exists(one.parts[#one.parts]..'.mjb'))
      assert(store.exists(two.parts[1]..'.mjb'))
      fail_index(true)
      index.slots.two=nil
      assert(not pcall(store.commit,store,index))
      fail_index(false)
      local restored=store:load_index()
      assert(restored.slots.two and store:get(restored.slots.two.snapshot)==b)
      local revised=STORE_MODULE.new(codec,store_host,string.rep('b',64),{store.identity})
      local migrated=revised:load_index()
      assert(migrated.identity==revised.identity and migrated.slots.two)
      assert(revised:accepts_identity(store.identity) and revised:get(migrated.slots.two.snapshot)==b)
      local unrelated=STORE_MODULE.new(codec,store_host,string.rep('c',64))
      assert(not pcall(unrelated.load_index,unrelated))
      corrupt(two.parts[1]..'.mjb')
      assert(not pcall(store.get,store,two))
      return {shared_prefix=true,distinct_suffix_identity=true,corruption_rejected=true,
        interrupted_index_preserved=true,orphan_slot_collected=true,
        proofreading_identity_compatible=true,unrelated_script_set_rejected=true,files=file_count()}
    '''))
    result["status"]="passed"
    write_json(ROOT/"reports/save_store_tests.json",result)
    print(result)
    return result


if __name__=="__main__":
    run()
