"""Focused native boundary checks and independent lifecycle programs."""
from __future__ import annotations
import json
from pathlib import Path
import struct
import sys
from audit_player_semantics import registered_providers,source_identity
from lua_bridge import table_value
from original_execution_remaining import ExecutionOracle
from original_text import TextOracle

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/"convert"))
from port_common import ROOT,archive_dir,timestamp,write_json


def run():
    before=source_identity()
    samples=json.loads((ROOT/"reports/opcode_dispatch.json").read_text(encoding="utf8"))["samples"]
    entries={s["sample"]:s["entries"]for s in
        json.loads((ROOT/"reports/syscall_dispatch.json").read_text(encoding="utf8"))["samples"]}
    selected=[samples[0],samples[-1]]
    native=[]
    for sample in selected:
        oracle=ExecutionOracle(sample,entries[sample["sample"]])
        at_end=oracle.lifecycle("$thread_atend")
        assert at_end["at_end"]==oracle.operand-2+200
        oracle=ExecutionOracle(sample,entries[sample["sample"]])
        execute=oracle.lifecycle("$exec_func")
        assert execute["pc"]==execute["target"]and execute["depth"]==0 and execute["old_cleanup_field"]==0
        restart={"scope":"inline older wrapper not exercised by this focused check"}
        if sample["sample"]=="RURINOKASANE":
            oracle=ExecutionOracle(sample,entries[sample["sample"]])
            restart=oracle.lifecycle("$reset_exec")
            assert restart["result"]==[(0,0)]and restart["reset_phase"]==1 and restart["child_phase"]==3
            if restart["reset_script"]is not None:assert restart["reset_script"]=="callee.mjo"
        native.append({"sample":sample["sample"],"exe_sha256":sample["exe_sha256"],
                       "thread_atend":at_end,"exec_func":execute,"reset_exec":restart})
    lua,_,_,system=registered_providers()
    mod=lua.execute((ROOT/"player/runtime/majiro_vm.lua").read_text(encoding="utf8"))
    ops=lua.execute((ROOT/"player/tables/opcodes.lua").read_text(encoding="utf8"))
    checks=table_value(lua.eval("""
    function(mod,ops,system)
      local scripts={
        ['library.mjo']={name='library.mjo',main=0,functions={{0x12345678,100}},
          code={{0,0x191},{100,0x800,43},{106,0x82b}}},
        ['start.mjo']={name='start.mjo',main=0,functions={{0x12345678,200},{99,300},{100,400}},
          code={{0,0x191},{100,0x800,0},{106,0x82b},{200,0x800,71},{206,0x82b},
            {300,0x800,0},{306,0x82b},{400,0x800,0},{406,0x82b}}},
        ['next.mjo']={name='next.mjo',main=0,functions={},code={{0,0x82c,0}}}}
      local resets={}
      local vm=mod.new{opcodes=ops,loader=function(n)return scripts[n]end,syscall=system.invoke,
        after_init=function(v,done,reason)resets[#resets+1]=reason;v:resume(done)end}
      vm:load('library');vm:begin('start')
      vm.globals[2][17]=31
      system.providers['$exec_func'](vm,{0x12345678,12},true)
      assert(#vm.frames==1 and vm:frame().script.name=='library.mjo')
      assert(vm:frame().script.code[vm:frame().index][1]==100 and vm:frame().args[1]==12)
      assert(vm.globals[2][17]==31)
      local keep={persistent=vm.globals[0],temporary=vm.globals[1]}
      vm.globals[0][17]=7;vm.globals[1][17]=9
      system.providers['$reset_exec'](vm,{'next'},true)
      assert(vm.exiting_context and vm.reset_reason==4 and vm.entry_script=='next.mjo')
      vm:run(100)
      assert(vm:frame().script.name=='next.mjo'and not vm.exiting_context)
      assert(vm.globals[0][17]==7 and vm.globals[1][17]==nil and resets[1]==4)
      local v=mod.new{opcodes=ops,loader=function(n)return scripts[n]end,syscall=system.invoke}
      v:begin('start')
      local child=v:spawn(99,{},false)
      v:select_thread(child)
      system.providers['$thread_atend'](v,{100})
      local exported=v:export_state()
      assert(exported.threads[child].at_end.offset==400)
      v:push(0);v:finish()
      assert(not v.threads[child].finished and v:frame().script.code[v:frame().index][1]==400)
      assert(v.threads[child].at_end==nil)
      v:push(0);v:finish()
      assert(v.threads[child].finished)
      return{exec_func_replaces_frames=true,exec_func_retains_thread_variables=true,
        reset_exec_unwinds_and_restarts_named_main=true,reset_preserves_persistent_bank=true,
        thread_atend_runs_once_after_root=true,thread_atend_serialized=true}
    end
    """)(mod,ops,system))
    sample=samples[-1];text=TextOracle(sample,entries[sample["sample"]])
    flag_cases=[]
    for command,moved in(("n",False),("N",True),("z",False)):
        text.configure()
        text.execute("syscallp",[],struct.pack("<IH",0x0542e1b7,0),budget=10000)
        original=text.instruction("ctrl",command)
        if command in("n","N"):assert(original["state"]["y"]!=30)==moved
        after=text.instruction("ctrl","n")
        assert after["state"]["y"]==(78 if command=="N"else 54)
        flag_cases.append({"control":command,"first_y":original["state"]["y"],"next_y":after["state"]["y"]})
    local=table_value(lua.eval("""
    function(system)
      local vm={frame=function()return{script={name='skip-fixture'}}end,
        resume=function()end}
      local c=system.state.console;c.configuration={1,20,30,120,300};c.font_spec={'',20,10,24}
      for _,cmd in ipairs{'n','N','z'}do
        c.x,c.y=20,30
        system.providers[0x0542e1b7](vm,{})
        system.text.execute(vm,'ctrl',cmd)
        assert(c.y==(cmd=='N'and 54 or 30))
        system.text.execute(vm,'ctrl','n')
        assert(c.y==(cmd=='N'and 78 or 54))
      end
      return true
    end
    """)(system))
    assert local
    report={"status":"passed","timestamp":timestamp(),"native_boundary_checks":native,"checks":checks,
        "scope":"two frame-layout families; lifecycle state and independent VM programs, not a full scheduler proof",
        "initial_newline_flag":flag_cases,
        "source_identity":before,"source_changed_during_test":source_identity()!=before}
    folder=archive_dir("execution_lifecycle")
    write_json(folder/(folder.name[:8]+"_execution_lifecycle.json"),report)
    write_json(ROOT/"reports/execution_lifecycle_tests.json",report)
    print("execution lifecycle: 5 native boundaries and 6 VM invariants passed")


if __name__=="__main__":run()
