"""Exercise production upgrade recovery, publication rollback and Java file I/O."""
from pathlib import Path
import hashlib
import json
import subprocess
import sys

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/"convert"))
from port_common import ROOT,archive_dir,timestamp,write_json,read_json
from lua_bridge import create_lua,table_value


def fixture():
    lua=create_lua();files={0:{},1:{}};failure={"index":False,"copy":False}
    legacy="a"*64;current="b"*64
    def host(request):
        if request.startswith("sha256\n"):return"OK "+hashlib.sha256(request[7:].encode()).hexdigest()
        parts=request.split("\n",1);head=parts[0].split(" ");data=parts[1]if len(parts)>1 else""
        op=head[0]
        if op=="save_legacy_identity":return"OK "+legacy
        if op=="save_legacy_catalog":
            rows=[{"root":root,"name":name}for root,items in files.items()for name in sorted(items)
                  if name=="index.mjs"or(name.startswith("index_")and name.endswith(".mjs"))]
            return"OK data\n"+json.dumps(rows)
        if op.startswith("save_legacy_"):
            root,name=int(head[1]),head[2]
            if op=="save_legacy_read":return"OK data\n"+files[root][name]if name in files[root]else"OK absent"
            if op=="save_legacy_copy":
                if failure["copy"]:raise IOError("copy failure")
                value=files[root][name]
                assert name not in files[0]or files[0][name]==value
                files[0][name]=value;return"OK"
        name=head[1]
        if op=="save_read":return"OK data\n"+files[0][name]if name in files[0]else"OK absent"
        if op=="save_exists":return"OK "+str(int(name in files[0]))
        if op=="save_write":
            if failure["index"]and name.startswith("index"):raise IOError("index publication failure")
            files[0][name]=data;return"OK"
        if op=="save_delete":del files[0][name];return"OK"
        raise AssertionError(request)
    def module(name):return lua.execute((ROOT/"player/runtime"/name).read_text(encoding="utf8"))
    codec=module("majiro_codec.lua");stores=module("majiro_store.lua")
    script=lua.table_from({"name":"story.mjo","source_sha256":"c"*64,"main":0,"functions":[],
                          "code":[[0,0x82c,0],[6,0x82b]]},recursive=True)
    vm_module=module("majiro_vm.lua")
    ops=lua.execute((ROOT/"player/tables/opcodes.lua").read_text(encoding="utf8"))
    def vm():
        return vm_module["new"](lua.table_from({"loader":lambda _:codec["clone"](script),
            "opcodes":ops,"syscall":lambda *args:None}))
    owner=vm();owner["begin"](owner,"story.mjo")
    packet=lua.table_from({"schema":1,"identity":legacy,"clock":10,"state":{
        "pages":{},"console":{},"settings":{},"input":{},"registry":{},"preference_bindings":{},
        "progress":{"pictures":{},"read":{}},"media":{"channels":{},"generation":0}}},recursive=True)
    packet["vm"]=owner["export_state"](owner)
    def validate(value,commands):
        module("majiro_save.lua")["validate_checkpoint"](value,commands)
        staging=vm();staging["import_state"](staging,codec["clone"](value["vm"]))
    def store(identity=current):
        return stores["new"](codec,host,identity,lua.table(),lua.table_from({"partition_foreign":True}))
    def seed(root=1,identity=legacy,slots=("slot_00101",),thumbnail=False,graphics=None):
        target=files[0];files[0]=files[root]
        try:
            old=store(identity);index=old["load_index"](old)
            for slot in slots:
                value=codec["clone"](packet);value["identity"]=identity
                record=lua.table_from({"snapshot":old["put"](old,codec["encode"](value)),
                                      "graphics":lua.table(),"info":"old chapter"})
                if graphics is not None:
                    record["graphics"][1]=old["put"](old,codec["encode"](graphics))
                if thumbnail:
                    name="thumb_"+"d"*64+".png";files[root][name]="PNG fixture";record["thumbnail"]=name
                index["slots"][slot]=record
            index["profile"]=lua.table_from({"marker":"old progress"})
            old["commit"](old,index)
        finally:files[0]=target
    def recovery(target):
        return module("majiro_save_compatibility.lua")["new"](target,host,module("majiro_json.lua"),
            lua.table(),validate)
    return locals()


def run():
    checks=[]
    f=fixture();f["seed"](thumbnail=True);old=dict(f["files"][1]);target=f["store"]()
    result=f["recovery"](target)["recover"]()
    index=target["load_index"](target)
    assert result["recovered"]==1 and index["slots"]["slot_00101"] and index["profile"]["marker"]=="old progress"
    slot=index["slots"]["slot_00101"]
    packet=f["codec"]["decode"](target["get"](target,slot["snapshot"]))
    assert packet["identity"]==f["current"]and f["files"][1]==old
    assert f["files"][0]["thumb_"+"d"*64+".png"]=="PNG fixture"
    checks.append("0.1 companion directory recovers checkpoint, profile and thumbnail without changing old bytes")
    index["slots"]["slot_00101"]=None;target["commit"](target,index)
    cold=f["store"]();f["recovery"](cold)["recover"]()
    assert cold["load_index"](cold)["slots"]["slot_00101"]is None
    checks.append("deleted recovered slots are not reimported after a cold startup")
    for root in (0,1):
        f=fixture();f["seed"](root=root,slots=("slot_00101","slot_00102"))
        old=dict(f["files"][root])
        target=f["store"]()
        if root==1:
            index=target["load_index"](target);index["slots"]["slot_00101"]=f["lua"].table_from({"info":"new chapter"})
            target["commit"](target,index)
        result=f["recovery"](target)["recover"]()
        index=target["load_index"](target)
        assert index["slots"]["slot_00102"]
        if root==1:assert index["slots"]["slot_00101"]["info"]=="new chapter"
        assert all(f["files"][root][name]==data for name,data in old.items())
    checks.append("same-directory foreign indexes and companion indexes recover while current occupied slots win")
    f=fixture();f["seed"]();target=f["store"]();f["failure"]["index"]=True
    try:f["recovery"](target)["recover"]()
    except IOError:pass
    else:raise AssertionError("publication failure ignored")
    assert not list(target["load_index"](target)["slots"])
    f["failure"]["index"]=False
    assert f["recovery"](f["store"]())["recover"]()["recovered"]==1
    checks.append("interrupted recovery publishes no slot or receipt and retries successfully")
    for kind in ("identity","script","offset","object","thumbnail","state","graphics"):
        f=fixture()
        if kind=="state":f["packet"]["state"]["media"]=None
        f["seed"](identity="e"*64 if kind=="identity"else f["legacy"],thumbnail=kind=="thumbnail",
                  graphics="batch\n5\npng 1"if kind=="graphics"else None)
        if kind in ("script","offset"):
            f["script"]["source_sha256"]="e"*64 if kind=="script"else"c"*64
            if kind=="offset":f["script"]["code"][1][1]=1
        if kind=="object":
            key=next(k for k in f["files"][1]if k.endswith(".mjb"));f["files"][1][key]="broken"
        if kind=="thumbnail":f["failure"]["copy"]=True
        old=dict(f["files"][1]);target=f["store"]();f["recovery"](target)["recover"]()
        assert not list(target["load_index"](target)["slots"])and f["files"][1]==old,kind
    checks.append("unrelated identities, changed scripts/offsets, corrupt objects/state/graphics and failed thumbnail copies retain old data")
    f=fixture();target=f["store"]();index=target["load_index"](target)
    index["profile"]=f["lua"].table_from({"marker":"committed"});target["commit"](target,index)
    index["profile"]["marker"]="uncommitted";f["failure"]["index"]=True
    try:target["commit"](target,index)
    except IOError:pass
    else:raise AssertionError("failure injection ignored")
    assert target["load_index"](target)["profile"]["marker"]=="committed"
    checks.append("failed index publication retains the last committed in-memory view")
    f=fixture();lua=f["lua"];target=f["store"]()
    def modules(path):
        actual=ROOT/("player/"+path if path.startswith("runtime/")else"player/tables/"+Path(path).name)
        return lua.execute(actual.read_text(encoding="utf8"))
    options=lua.table_from({"names":modules("scripts/syscall_names.lua"),
        "config_files":{},"config_lines":{},"environment":{},"resource_names":{},
        "source_identity":f["current"],"width":800,"height":600},recursive=True)
    options["modules"]=modules;options["store"]=target
    options["now"]=lambda:0;options["set_clock"]=lambda value:None
    options["graphics"]=lambda request:"OK";options["audio"]=lambda *args:True
    options["cp932"]=lambda text:text.encode("cp932")
    system=modules("runtime/majiro_syscalls.lua")["new"](options)
    owner=f["vm"]();owner["begin"](owner,"story.mjo")
    def call(name,*args):return system["providers"][name](owner,lua.table_from(args))
    call("$save_point","chapter 3");call("$do_save",101)
    f["failure"]["index"]=True
    for operation,args in (("$do_save",(102,)),("$copy_save",(101,102)),
                           ("$delete_save",(101,)),("$set_save_info",(101,"changed"))):
        try:call(operation,*args)
        except IOError:pass
        else:raise AssertionError(operation+" ignored publication failure")
        assert system["state"]["save_catalog"]["slot_00101"]["info"]=="chapter 3"
        assert system["state"]["save_catalog"]["slot_00102"]is None
        disk=target["load_index"](target)
        assert disk["slots"]["slot_00101"]["info"]=="chapter 3"and disk["slots"]["slot_00102"]is None
    f["failure"]["index"]=False
    call("$copy_save",101,102);call("$delete_save",101)
    assert system["state"]["save_catalog"]["slot_00101"]is None
    assert system["state"]["save_catalog"]["slot_00102"]["info"]=="chapter 3"
    checks.append("real save/copy/delete/rename providers retain visible slots on publication failure and recover on retry")
    folder=archive_dir("save_upgrade");classes=folder/"classes";classes.mkdir()
    java=Path(read_json(ROOT/"reports/android_toolchain.json")["java"])/"bin"
    jar=ROOT/"references/player/testing-json/json-20240303.jar"
    production=ROOT/"player/android/src/org/majiro/player"
    commands=[
        [java/"javac.exe","-encoding","UTF-8","-cp",jar,"-d",classes,
         production/"SaveMigration.java",production/"ArchiveLayerPolicy.java",
         ROOT/"scripts/analysis/SaveMigrationChecks.java"],
        [java/"java.exe","-cp",str(classes)+";"+str(jar),"org.majiro.player.SaveMigrationChecks",folder/"files"],
    ]
    for number,command in enumerate(commands):
        result=subprocess.run(list(map(str,command)),capture_output=True,timeout=40)
        (folder/f"20261114_java_{number}.txt").write_bytes(result.stdout+result.stderr)
        assert result.returncode==0,(result.stdout+result.stderr).decode("utf8",errors="replace")
    report={"status":"passed","timestamp":timestamp(),"checks":checks,"java":json.loads(result.stdout),
            "device_accessed":False,"existing_player_saves_modified":False,"evidence":str(folder.relative_to(ROOT))}
    write_json(ROOT/"reports/save_upgrade_tests.json",report);write_json(folder/"report.json",report)
    print(json.dumps(report,ensure_ascii=True))
    return 0


if __name__=="__main__":raise SystemExit(run())
