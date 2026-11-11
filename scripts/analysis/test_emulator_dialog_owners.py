"""Reject obsolete setup/checker responses before they mutate live state."""
from pathlib import Path
import json
import sys

from audit_player_semantics import registered_providers
from player_paths import player_path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]/"convert"))
from port_common import ROOT, timestamp, write_json


def run():
    checks = []
    for operation in ("setup", "checker"):
        for change in ("wait_changed", "terminate", "generation"):
            actions = []

            def dialog(action, *args):
                actions.append(action)
                if action == "begin":
                    return "pending"
                if action == "cancel":
                    return "OK"
                return "data " + b"musicvolume=17\nvoicevolume=19".hex()

            def service(action, *args):
                actions.append(action)
                return {"checker_begin": "pending", "checker_cancel": "OK",
                        "checker_poll": "authenticated 9004"}[action]

            lua, _, _, system = registered_providers(overrides={"dialog": dialog, "service": service})
            module = lua.execute(player_path("runtime/majiro_vm.lua").read_text("utf8"))
            ops = lua.execute(player_path("resources/scripts/opcodes.lua").read_text("utf8"))
            vm = lua.eval("""function(mod,ops,system)
              local script={name='obsolete-dialog',main=0,functions={},code={{0,0x191}}}
              local vm=mod.new{opcodes=ops,loader=function()return script end,syscall=system.invoke}
              vm:begin(script.name);return vm
            end""")(module, ops, system)
            system["state"]["settings"]["musicvolume"] = 777
            system["state"]["settings"]["voicevolume"] = 888
            name = "$volume_setup_dialog" if operation == "setup" else 0xf430015b
            system["providers"][name](vm, lua.table(), True)
            if change == "wait_changed":
                vm["waiting"] = None
            elif change == "terminate":
                vm["threads"][1]["terminate_requested"] = True
            else:
                vm["context_generation"] += 1
            ok, error = lua.eval("function(s,v)return pcall(s.poll,v,{})end")(system, vm)
            assert not ok
            assert system["state"]["settings"]["musicvolume"] == 777
            assert system["state"]["settings"]["voicevolume"] == 888
            assert not system["state"]["external_checker_authenticated"]
            assert ("cancel" if operation == "setup" else "checker_cancel") in actions
            assert ("poll" if operation == "setup" else "checker_poll") not in actions
            checks.append(operation + ": " + change + " cancelled before polling or applying response")
    report = {"status": "passed", "timestamp": timestamp(), "checks": checks,
              "runtime_root": "player/runtime", "device_accessed": False}
    folder = ROOT.parent/"archive/2026-11/20261111_emulator_audit_host"
    folder.mkdir(parents=True, exist_ok=True)
    write_json(folder/"20261111_dialog_owners.json", report)
    print(json.dumps(report))


if __name__ == "__main__":
    run()
