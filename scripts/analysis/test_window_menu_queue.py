"""Check native menu commands against source permissions and host slice safety."""
from pathlib import Path
import sys

sys.path[:0] = [str(Path(__file__).resolve().parents[1] / "convert")]
from port_common import ROOT, archive_dir, timestamp, write_json
from audit_player_semantics import registered_providers


HARNESS = """
function(mod,ops,system,menus_module)
  local log={}
  local script={name='window-menu-fixture.mjo',main=0,functions={},
    code={{0,0x191},{2,0x82c,0},
      {100,0x835,0x1234,0},{104,0x800,0},{110,0x82b},
      {120,0x835,0x1235,0},{124,0x800,0},{130,0x82b}}}
  local vm=mod.new{opcodes=ops,loader=function()return script end,
    syscall=function(vm,hash,args)
      log[#log+1]={hash=hash,owner=vm.thread_id};return 0
    end}
  vm:begin(script.name);vm.waiting={kind='time',until_ms=5000}
  system.state.events.LOADMENU={{owner=1,script=script.name,offset=100}}
  system.state.events.SAVEMENU={{owner=1,script=script.name,offset=120}}
  system.state.events.CONFIGMENU={{owner=1,script=script.name,offset=100}}
  return vm,log,menus_module.new(system.state,system.dispatch_event)
end
"""


def main():
    folder = archive_dir("window_menu_queue")
    cases = {
        "load": "assert(m.request(v,1)=='queued');m.poll(v);v:run(256);assert(#log==1 and log[1].hash==0x1234)",
        "save": "assert(m.request(v,3)=='queued');m.poll(v);v:run(256);assert(#log==1 and log[1].hash==0x1235)",
        "config": "assert(m.request(v,99)=='queued');m.poll(v);v:run(256);assert(#log==1)",
        "unsupported": "assert(m.request(v,4)=='unsupported');m.poll(v);assert(#v.frames==1)",
        "source_disabled": "s.settings.save_menu=false;assert(m.request(v,1)=='disabled');assert(m.request(v,3)=='disabled')",
        "mouse_disabled": "s.settings.mouse=false;assert(m.request(v,99)=='disabled')",
        "missing_source_event": "s.events.SAVEMENU=nil;assert(m.request(v,3)=='unsupported')",
        "non_yielding_budget_slice": """
          v.waiting=nil;assert(m.request(v,3)=='queued');m.poll(v);assert(#v.frames==1)
          v.waiting={kind='time',until_ms=5000};m.poll(v);v:run(256)
          assert(#log==1 and log[1].hash==0x1235)
        """,
        "draw_transaction": """
          s.invalidate_depth=1;assert(m.request(v,3)=='queued');m.poll(v)
          assert(#v.frames==1 and s.invalidate_depth==1)
          s.invalidate_depth=0;m.poll(v);v:run(256);assert(#log==1)
        """,
        "modal_lock_retains_accepted_command": """
          assert(m.request(v,3)=='queued');s.modal_stack={{}};m.poll(v);assert(#v.frames==1)
          s.modal_stack={};m.poll(v);v:run(256);assert(#log==1)
        """,
        "new_request_while_modal": "s.modal_stack={{}};assert(m.request(v,3)=='disabled');assert(#v.frames==1)",
        "latest_command_and_one_delivery": """
          assert(m.request(v,1)=='queued');assert(m.request(v,3)=='queued')
          m.poll(v);v:run(256);m.poll(v);v:run(256)
          assert(#log==1 and log[1].hash==0x1235)
        """,
        "restart_cancels_pending_host_input": """
          assert(m.request(v,3)=='queued');v.context_generation=v.context_generation+1
          m.poll(v);v:run(256);assert(#log==0)
        """,
        "dead_owner": "v.threads[1].finished=true;assert(m.request(v,3)=='unsupported')",
        "wait_and_permissions_preserved": """
          assert(m.request(v,3)=='queued');m.poll(v);v:run(256)
          assert(#log==1 and v.waiting.until_ms==5000 and #v.frames==1)
          assert(s.settings.save_menu==nil and s.settings.mouse==nil)
        """,
    }
    report = {"timestamp": timestamp(), "status": "running", "cases": [],
              "device_accessed": False,
              "scope": "actual menu queue and event dispatcher; source disable flags, draw locks, waits and restarts",
              "evidence": str(folder.relative_to(ROOT))}
    try:
        for prefix in ("player/runtime", "runtime"):
            for name, code in cases.items():
                lua, _, _, system = registered_providers()
                vm_module = lua.execute((ROOT / "player/runtime/majiro_vm.lua").read_text("utf8"))
                ops = lua.execute((ROOT / "player/tables/opcodes.lua").read_text("utf8"))
                module = lua.execute((ROOT / prefix / "majiro_window_menus.lua").read_text("utf8"))
                vm, log, menus = lua.eval(HARNESS)(vm_module, ops, system, module)
                lua.globals()["v"], lua.globals()["log"] = vm, log
                lua.globals()["s"], lua.globals()["m"] = system["state"], menus
                lua.execute(code)
                report["cases"].append({"runtime": prefix, "case": name, "status": "passed"})
        report["status"] = "passed"
    except Exception as error:
        report.update(status="failed", error=str(error))
        raise
    finally:
        write_json(folder / "report.json", report)
        write_json(ROOT / "reports/window_menu_queue.json", report)
    print("Window menu queue: 30 permission, modal, transaction and lifecycle cases passed")


if __name__ == "__main__":
    main()
