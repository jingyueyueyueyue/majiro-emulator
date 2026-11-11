"""Original writer -> real MJO verified root -> continue -> portable cold restore."""
from __future__ import annotations
import argparse
import hashlib,json,os,struct,subprocess,sys
from pathlib import Path

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/"convert"))
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/"build"))
from build_player_core import msvc_environment
from majiro_arc import Archive
from majiro_mjo import Script,opcode_table
from majiro_story_save import parse_story_save
from majiro_native_save import parse_system,SYSTEM_SIGNATURE,SYSTEM_HEADER,parse_readmarks
from port_common import ROOT,archive_dir,timestamp,write_json
from original_story_save import StorySaveOracle,LegacyStoryWriter
from lua_bridge import create_lua


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--sample",choices=("narci_onetwo","narci_himeko"),default="narci_onetwo")
    args=parser.parse_args();legacy=args.sample=="narci_himeko"
    evidence=archive_dir("native_import_resume")
    dispatch=json.loads((ROOT/"reports/opcode_dispatch.json").read_text(encoding="utf8"))["samples"]
    sample=next(row for row in dispatch if row["sample"]==args.sample)
    games=json.loads((ROOT/"reports/majiro_samples.json").read_text(encoding="utf8"))["games"]
    game=next(row for row in games if Path(row["directory"]).name==args.sample)
    archive=Archive(Path(game["directory"])/"scenario.arc")
    selected=None
    for member in archive.members:
        if member.extension!=".mjo"or not member.name.isascii():continue
        parsed=Script(member.name,member.read())
        if parsed.line_count==0 or not any(ins.name=="text"for ins in parsed.instructions):continue
        for index,row in enumerate(parsed.instructions[:-1]):
            if row.name=="ldc.i"and parsed.instructions[index+1].name=="ret":
                selected=(member,parsed,row);break
        if selected:break
    assert selected
    member,script,row=selected
    oracle=(LegacyStoryWriter if legacy else StorySaveOracle)(sample);oracle.fixture(0,False)
    previous_line=0
    for instruction in script.instructions:
        if instruction.offset>=row.offset:break
        if instruction.name=="line":previous_line=instruction.operands[0]
    if legacy:
        frame=oracle.FRAME_BLOCKS
        oracle.machine.mem_write(frame,bytes(168))
        oracle.machine.mem_write(frame,member.name.encode("cp932")+b"\0")
        oracle.write_u32(frame+128,row.offset);oracle.write_u32(frame+144,previous_line)
        oracle.write_u32(oracle.HEADER+0x90+20,0)
    else:
        record=oracle.DATA+0x40000;code=oracle.DATA+0x50000;frame=oracle.context+0x28
        oracle.machine.mem_write(record,bytes(128))
        oracle.machine.mem_write(record,member.name.encode("cp932")+b"\0")
        oracle.write_u32(record+0x90,len(script.code))
        oracle.machine.mem_write(code,script.code)
        oracle.source_scripts={member.name.encode("cp932"):record}
        oracle.write_u32(frame,code+row.offset)
        for offset in(4,8,12,0x14,0x18,0x1c,0x20,0x24):oracle.write_u32(frame+offset,0)
        oracle.write_u32(frame+0x10,previous_line)
        oracle.write_u32(0x4E6848,0)
        oracle.machine.mem_write(0x54D820,bytes(40000))
    # Fixture has no live media/scene. Its decoded state is intentionally
    # neutral and the importer rejects this if any opaque source field changes.
    for at in (0x4E53D8,0x4E53E4,0x4E53C8,0x4E53D4,0x4E53FC,0x4E53E0,0x4E53C4):
        oracle.write_u32(at,0)
    native=oracle.capture_and_write()
    save_path=evidence/"20261110_original_writer_idle_root.sav";save_path.write_bytes(native)
    script_path=evidence/"20261110_original_source.mjo";script_path.write_bytes(member.read())
    descriptor=parse_story_save(native);descriptor["source_sha256"]=hashlib.sha256(native).hexdigest()
    assert not any(bytes.fromhex(descriptor["header_bytes_hex"])[0x22c:0x920])
    # Minimal system/readmark files exercise the same strict production readers.
    system_header=SYSTEM_SIGNATURE+struct.pack("<II",1,0)+bytes(SYSTEM_HEADER-24)
    system_bytes=system_header+struct.pack("<4I",0x13572468,0,0,73)
    system_native=parse_system(system_bytes)
    name=member.name.upper().encode("cp932");count=script.line_count
    marked_text_line=0;marked_text=None
    for ins in script.instructions:
        if ins.name=="line":marked_text_line=ins.operands[0]
        elif ins.name=="text"and 0<marked_text_line<count:
            marked_text=(marked_text_line,ins.offset);break
    assert marked_text
    bits=bytearray((count+7)//8+16)
    for marked_line in(previous_line,marked_text[0]):
        if 0<=marked_line<count:bits[marked_line>>3]|=1<<(marked_line&7)
    readmark_bytes=name+bytes(128-len(name))+struct.pack("<5I",len(bits),count,0,0,0)+bytes(bits)
    marks_native=parse_readmarks(readmark_bytes)
    lua=create_lua();files={};clock={"ms":0};transaction={"active":False}
    def module(name):
        path=ROOT/("player/"+name if name.startswith("runtime/")else
                   "player/tables/"+name.split("/")[-1]if name.startswith("scripts/")else name)
        return lua.execute(path.read_text(encoding="utf8"))
    def host(request):
        if request.startswith("sha256\n"):return"OK "+hashlib.sha256(request[7:].encode()).hexdigest()
        action,_,tail=request.partition(" ");name,_,data=tail.partition("\n")
        if action=="save_exists":return"OK "+str(int(name in files))
        if action=="save_read":return"OK data\n"+files[name]if name in files else"OK absent"
        if action=="save_write":files[name]=data;return"OK"
        if action=="save_delete":files.pop(name,None);return"OK"
        raise RuntimeError("unexpected native import storage operation")
    codec=module("runtime/majiro_codec.lua")
    store=module("runtime/majiro_store.lua")["new"](codec,host,"a"*64)
    def graphics(request):
        if request=="checkpoint_begin":transaction["active"]=True
        elif request in("checkpoint_commit","checkpoint_rollback"):transaction["active"]=False
        elif request!="reset_pages":raise RuntimeError("unexpected import graphics command")
        return"OK"
    def make_options():
        options=lua.table_from({"names":module("scripts/syscall_names.lua"),"source_identity":"a"*64,
            "config_files":{},"config_lines":{},"environment":{},"resource_names":{},
            "width":1280,"height":720},recursive=True)
        options["modules"]=module;options["store"]=store;options["now"]=lambda:clock["ms"]
        options["set_clock"]=lambda value:clock.update(ms=value)
        options["graphics"]=graphics;options["audio"]=lambda *args:True
        options["cp932"]=lambda value:value.encode("cp932")
        options["decode_bytes"]=lambda value:value.encode("latin1").decode("cp932")
        return options
    options=make_options()
    system=module("runtime/majiro_syscalls.lua")["new"](options)
    source_table={"name":member.name,"source_sha256":script.sha256,"main":script.main,
                  "line_count":script.line_count,"code_size":script.code_size,"functions":script.functions,
                  "code":[[ins.offset,ins.opcode,*ins.operands]for ins in script.instructions]}
    script_lua=lua.table_from(source_table,recursive=True)
    loader_state={"changed":False}
    def load_script(name):
        result=codec["clone"](script_lua)
        if loader_state["changed"]:result["source_sha256"]="f"*64
        return result
    def new_vm(system,persistent=None,array_stride=False):
        return module("runtime/majiro_vm.lua")["new"](lua.table_from({
            "loader":load_script,"opcodes":module("scripts/opcodes.lua"),"syscall":system["invoke"],
            "array_3d_legacy_stride":array_stride,
            "numeric_profile":"x87"if legacy else"sse32","persistent_globals":persistent}))
    vm=new_vm(system)
    vm["begin"](vm,member.name)
    manifest=lua.table_from({"schema":1,"source_identity":"a"*64,"native_exe_sha256":sample["exe_sha256"],
        "save_sha256":descriptor["source_sha256"],"verified_same_script_version":True,
        "scripts":{member.name.lower():{"sha256":script.sha256,"version_identity":"a"*64}},
        "boundaries":{member.name.lower():{row.offset:"idle_root"}}},recursive=True)
    saves=system["saves"]
    state=system["state"]
    lua.eval("""function(s)
      s.files[42]={name='stale'};s.events.stale={{hash=123}}
      s.modal_stack={{thread=42}};s.screen_snapshot=99;s.current_thumbnail=99;s.pages[99]={width=1,height=1}
      s.console.text='stale';s.console.last_message='stale';s.console.configuration={99,0,0,1,1}
      s.media.music_stack={{file='stale'}};s.media.old_request='stale'
      s.input.cursor_override={x=1,y=2};s.settings.save=false;s.settings.save_menu=false
      s.settings.fast_mode=true;s.screen.zoom_extent=999;s.active_ui_script='stale'
    end""")(state)
    plan=saves["plan_native_import"](vm,lua.table_from(descriptor,recursive=True),manifest,
                                   lua.table_from(system_native,recursive=True),
                                   lua.table_from(marks_native,recursive=True))
    assert plan["identity_verified"]
    # Fresh source verification happens before graphics or VM mutation.
    loader_state["changed"]=True
    failed=False
    try:saves["import_native"](vm,plan)
    except Exception:failed=True
    assert failed and state["console"]["text"]=="stale"and not transaction["active"]
    loader_state["changed"]=False
    original_status=plan["packet"]["vm"]["status"]
    plan["packet"]["vm"]["status"]="finished"
    failed=False
    try:saves["import_native"](vm,plan)
    except Exception:failed=True
    assert failed and state["console"]["text"]=="stale"and not transaction["active"]
    plan["packet"]["vm"]["status"]=original_status
    assert saves["import_native"](vm,plan)==0
    assert vm["frame"](vm)["script"]["code"][vm["frame"](vm)["index"]][1]==row.offset
    assert vm["globals"][0][0x13572468]==73
    assert vm["globals"][1][0x11111111]==-2
    assert not transaction["active"]
    lua.eval("""function(s)
      assert(next(s.files)==nil and next(s.events)==nil and #s.modal_stack==0)
      assert(s.screen_snapshot==nil and s.current_thumbnail==nil and next(s.pages)==nil)
      assert(s.console.text==''and s.console.last_message==''and s.console.configuration==nil)
      assert(#s.media.music_stack==0 and s.media.old_request==nil and s.input.cursor_override==nil)
      assert(s.settings.save~=false and s.settings.save_menu~=false and not s.settings.fast_mode)
      assert(s.screen.zoom_extent==nil and s.active_ui_script==nil and next(s.tiles.memory)==nil)
    end""")(state)
    assert state["progress"]["lines"][script.sha256+":"+str(marked_text[0])]
    assert state["progress"]["read"][member.name+":"+str(marked_text[1])]
    system["providers"]["$do_save"](vm,lua.table_from([1]))
    vm["step"](vm);vm["step"](vm)
    assert vm["status"]=="finished"
    # Dispose the original provider/VM and reconstruct from only stored data.
    profile=store["load_index"](store)["profile"]
    system=module("runtime/majiro_syscalls.lua")["new"](make_options())
    vm=new_vm(system,profile["persistent_globals"]);vm["begin"](vm,member.name)
    system["providers"]["$do_load"](vm,lua.table_from([1]))
    assert vm["status"]=="running"and vm["globals"][0][0x13572468]==73
    vm["step"](vm);vm["step"](vm);assert vm["status"]=="finished"
    # Rejections occur during plan creation, before any real VM import.
    invalid=lua.table_from(descriptor,recursive=True);invalid["frames"][1]["fields"][6]=1
    failed=False
    try:saves["plan_native_import"](vm,invalid,manifest)
    except Exception:failed=True
    assert failed and vm["status"]=="finished"
    # This neutral continuation does not index arrays. Exercise both declared
    # VM layouts solely to verify importer/staging propagation, without claiming
    # the original sample itself uses the alternate layout.
    for stride in(False,True):
        target=new_vm(system,array_stride=stride);target["begin"](target,member.name)
        imported=system["saves"]["plan_native_import"](target,
            lua.table_from(descriptor,recursive=True),manifest)
        assert imported["packet"]["vm"]["array_3d_legacy_stride"]==stride
        system["saves"]["import_native"](target,imported)
        assert target["export_state"](target)["array_3d_legacy_stride"]==stride
    env=msvc_environment();compiler=Path(env["VCTOOLSINSTALLDIR"])/"bin/Hostx64/x64/cl.exe"
    definitions=opcode_table()
    header="#pragma once\n#include <array>\n#include <cstdint>\nnamespace majiro{struct Opcode{uint16_t code;const char* encoding;};\n"
    header+=f"inline constexpr std::array<Opcode,{len(definitions)}> kOpcodes={{{{"
    header+=",".join(f'{{{code},\"{encoding}\"}}'for code,(_,encoding)in definitions.items())+"}};}\n"
    (evidence/"majiro_opcodes.hpp").write_text(header,encoding="ascii")
    executable=evidence/"native_import_probe.exe"
    command=[str(compiler),"/nologo","/std:c++20","/O2","/EHsc","/W4","/WX","/MT","/utf-8",
             "/I"+str(evidence),str(ROOT/"player/core/majiro_core.cpp"),
             str(ROOT/"player/core/majiro_save.cpp"),str(ROOT/"player/core/majiro_story_save.cpp"),
             str(ROOT/"player/core/majiro_native_import.cpp"),
             str(ROOT/"scripts/analysis/native_import_probe.cpp"),
             "/Fo"+str(evidence)+os.sep,"/Fe"+str(executable)]
    process=subprocess.run(command,cwd=evidence,env=env,capture_output=True)
    (evidence/"20261110_compile.txt").write_bytes(process.stdout+process.stderr)
    if process.returncode:raise RuntimeError("native import core compile failed: "+str(evidence))
    output=subprocess.check_output([str(executable),str(save_path),str(script_path),member.name,str(row.offset)],text=True)
    assert output.split()==[str(row.offset),script.sha256,descriptor["source_sha256"]]
    report={"schema":1,"timestamp":timestamp(),"status":"passed",
        "original_exe_sha256":sample["exe_sha256"],"original_writer_generated_save_sha256":descriptor["source_sha256"],
        "real_mjo":{"archive":str(archive.path),"name":member.name,"sha256":script.sha256,"offset":row.offset},
        "continued_original_script":True,"portable_save_and_cold_restore":True,
        "system_values_merged":True,"readmark_lines_merged":True,"readmark_text_offsets_merged":True,
        "unsupported_handler_rejected":True,"stale_scene_reset":True,"plan_tamper_rejected":True,
        "source_change_before_commit_rejected":True,"new_provider_vm_cold_restore":True,
        "array_index_profile_propagation":[False,True],
        "native_import_scope":"explicitly verified idle root with neutral scene and empty stack",
        "full_original_game_save_import":False,"device_tests_executed":False,
        "evidence":str(evidence.relative_to(ROOT)),
        "source_sha256":{name:hashlib.sha256((ROOT/name).read_bytes()).hexdigest()for name in(
            "player/core/majiro_native_import.hpp","player/core/majiro_native_import.cpp",
            "player/runtime/majiro_native_import.lua","player/runtime/majiro_save.lua",
            "scripts/analysis/test_native_import_resume.py")}}
    target=ROOT/"reports/native_import_resume_tests.json"
    previous=json.loads(target.read_text(encoding="utf8"))if target.exists()else{}
    report["sample"]=args.sample
    record={key:value for key,value in report.items()if key!="samples"}
    by_name={item["sample"]:item for item in previous.get("samples",[])}
    by_name[args.sample]=record;report["samples"]=list(by_name.values())
    write_json(target,report)
    write_json(evidence/"20261110_native_import_resume_tests.json",report)
    print("native import: real MJO continuation, system/readmark merge and portable cold restore passed")


if __name__=="__main__":main()
