"""Replay menu input using the installed Lua 5.1 VM; no device or game writes."""
from __future__ import annotations

from datetime import datetime
import hashlib
import json
from pathlib import Path
import sys

from lupa.lua51 import LuaRuntime

ROOT = Path(__file__).resolve().parents[2]
BASE = ROOT / "player/runtime"
FIXED = BASE / "majiro_input.lua"

CASES = {
    "reset_clears_focus_then_enter": """
        setarea(1);setarea(2);thread.area_focus=2
        call('$areaevent_reset',{})
        defaults();poll({pressed={[13]=true}})
        check()
        assert(thread.area_focus==nil)
        assert(#calls==1 and calls[1].offset==40)
    """,
    "shrunk_menu_rejects_stale_focus": """
        setarea(1);thread.area_focus=8
        poll({pressed={[13]=true}});check()
        assert(thread.area_focus==1)
        assert(#calls==2 and calls[2].offset==20)
    """,
    "empty_menu_arrow_then_enter": """
        defaults();poll({pressed={[40]=true,[13]=true}});check()
        assert(thread.area_focus==nil)
        assert(#calls==1 and calls[1].offset==40)
    """,
    "empty_menu_up_then_enter": """
        defaults();poll({pressed={[38]=true,[13]=true}});check()
        assert(thread.area_focus==nil and #calls==1 and calls[1].offset==40)
    """,
    "empty_menu_without_default": """
        poll({pressed={[37]=true,[32]=true}});check()
        assert(#calls==0 and thread.area_focus==nil)
    """,
    "fractional_focus_is_invalid": """
        setarea(1);thread.area_focus=1.5
        poll({pressed={[13]=true}});check()
        assert(thread.area_focus==1 and #calls==2)
    """,
    "nan_focus_is_invalid": """
        setarea(1);thread.area_focus=0/0
        poll({pressed={[13]=true}});check()
        assert(thread.area_focus==1 and #calls==2)
    """,
    "focus_next_wraps": """
        setarea(1);setarea(2);thread.area_focus=2
        poll({pressed={[40]=true}});check()
        assert(thread.area_focus==1 and #calls==1)
    """,
    "focus_previous_wraps": """
        setarea(1);setarea(2);thread.area_focus=1
        poll({pressed={[38]=true}});check()
        assert(thread.area_focus==2 and #calls==1)
    """,
    "focused_enter_keeps_callback_order_and_types": """
        setarea(1);setarea(2);thread.area_focus=2
        poll({pressed={[13]=true}});check()
        assert(#calls==2 and calls[1].offset==11 and calls[2].offset==21)
        local args=calls[2].args
        assert(#args==5 and args[1]==2 and args[2]==40 and args[5]==30)
        assert(args._types[1]==0 and args._types[5]==0)
        assert(state.input.pressed[13]==nil)
    """,
    "pointer_hover_click_and_leave": """
        setarea(1);setarea(2)
        poll({x=12.75,y=13.5,buttons=1});check()
        assert(#calls==2 and calls[1].offset==10 and calls[2].offset==20)
        assert(state.input.x==12 and state.input.y==13 and state.input.buttons==0)
        calls={}
        poll({x=45.9,y=15.9,buttons=1});check()
        assert(#calls==1 and calls[1].offset==30)
        assert(state.input.buttons==1)
        calls={};check()
        assert(#calls==2 and calls[1].offset==11 and calls[2].offset==21)
        assert(state.input.buttons==0)
    """,
    "reverse_hover_handoff_preserves_confirmation": """
        setarea(1);setarea(2)
        thread.areas[2].inside=true
        poll({x=12,y=15,pressed={[13]=true}});check()
        assert(#calls==1 and calls[1].offset==31)
        assert(state.input.pressed[13]==true and not thread.areas[1].inside)
        calls={};check()
        assert(#calls==2 and calls[1].offset==10 and calls[2].offset==20)
        assert(state.input.pressed[13]==nil)
    """,
    "leave_without_callback_still_precedes_enter": """
        setarea(1);setarea(2)
        thread.areas[2].inside=true;thread.areas[2].args[7]=0
        poll({x=12,y=15});check()
        assert(#calls==0 and not thread.areas[1].inside)
        calls={};check()
        assert(#calls==1 and calls[1].offset==10)
    """,
    "right_click_uses_original_default_with_integer_coords": """
        setarea(1);defaults()
        poll({x=12.75,y=13.5,buttons=2});check()
        assert(#calls==2 and calls[2].offset==50)
        assert(#calls[2].args==2 and calls[2].args[1]==12 and calls[2].args[2]==13)
        assert(state.input.buttons==0)
    """,
    "mouse_disabled_cannot_inject_callback": """
        setarea(1);defaults();state.settings.mouse=false
        poll({buttons=2,pressed={[13]=true}});check()
        assert(#calls==0 and state.input.buttons==0 and next(state.input.pressed)==nil)
    """,
    "keyboard_disabled_cannot_confirm": """
        setarea(1);state.settings.keyboard_disabled=true
        poll({pressed={[13]=true}});check()
        assert(#calls==0)
    """,
    "unresolved_callback_stays_an_error": """
        setarea(1)
        thread.areas[1].args[8]=999999
        poll({x=12,y=13,buttons=1})
        local ok,err=pcall(check)
        assert(not ok and tostring(err):find('area callback not present',1,true))
    """,
    "menu_focus_is_per_thread": """
        setarea(1);thread.area_focus=1
        vm.threads[2]={id=2,areas={},area_focus=9}
        vm.thread_id=2
        call('$areaevent_reset',{})
        assert(vm.threads[1].area_focus==1 and vm.threads[2].area_focus==nil)
    """,
}

HARNESS = r"""
local define_map={}
local state={settings={mouse=true},reading_wait=false}
local calls={}
local vm_module=assert(loadstring(VM_SOURCE))()
local vm=vm_module.new{
  loader=function()error('unexpected loader')end,
  opcodes={},syscall=function()error('unexpected syscall')end,
}
local script={name='recall_menu_fixture.mjo',functions={}}
for i=1,2 do
  script.functions[#script.functions+1]={100+i-1,10+i-1}
  script.functions[#script.functions+1]={200+i-1,20+i-1}
  script.functions[#script.functions+1]={300+i-1,30+i-1}
end
script.functions[#script.functions+1]={400,40}
script.functions[#script.functions+1]={500,50}
vm.scripts[script.name]=script
vm.frames[1]={script=script,args={},locals={},system={}}
local thread=vm.threads[1]
-- Capture only the dispatcher boundary. Argument copying and failure reporting
-- are the installed VM implementations, including their type metadata.
vm.resume=function(self,action)
  assert(action.kind=='sequence')
  for _,call in ipairs(action.calls)do calls[#calls+1]=call end
end
local module=assert(loadstring(INPUT_SOURCE))()
local input=module.install(function(name,fn)define_map[name]=fn end,state,
    {now=function()return 1000 end})
local function call(name,args)return assert(define_map[name])(vm,args,false)end
local function poll(event)input.sample(event)end
local function check()call('$areaevent_check',{})end
local function defaults()call('$areaevent_setdefault',{400,500})end
local function setarea(index)
  local args={index,10+(index-1)*30,10,25,30,100+index-1,300+index-1,200+index-1}
  args._types={0,0,0,0,0,0,0,0}
  call('$areaevent_set',args)
end
"""


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def run_case(source: Path, case: str) -> dict:
    lua = LuaRuntime(unpack_returned_tuples=True)
    lua.globals()["VM_SOURCE"] = (BASE / "majiro_vm.lua").read_text(encoding="utf8")
    lua.globals()["INPUT_SOURCE"] = source.read_text(encoding="utf8")
    try:
        lua.execute(HARNESS + "\n" + CASES[case])
    except Exception as error:
        # Test fixtures have no game dialogue, preferences or API configuration.
        return {"case": case, "passed": False, "error": str(error).splitlines()[0]}
    return {"case": case, "passed": True}


def main() -> int:
    results = [run_case(FIXED, name) for name in CASES]
    passed = all(row["passed"] for row in results)
    report = {"status": "passed" if passed else "failed", "cases": results,
              "device_accessed": False, "runtime_sha256": sha(FIXED),
              "scope": "real production Lua 5.1 input and VM; synthetic menu callbacks"}
    folder = ROOT / "reports"
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "menu_focus_semantics_tests.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf8")
    print(f"Production menu focus: {sum(row['passed'] for row in results)}/{len(results)} passed")
    return not passed


if __name__ == "__main__":
    sys.exit(main())
