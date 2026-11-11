"""Process-wide Majiro variables survive old slots and cold initialization."""
from model_session import Session
from lua_bridge import table_value
from test_settings import click_id
from port_common import ROOT, archive_dir, write_json


def run():
    evidence = archive_dir("persistent_globals")
    s = Session(False, save_root=evidence / "saves")
    assert s.advance() == "ui" and s.click(1) == "text", s.summary()
    s.system["providers"]["$do_save"](s.vm, s.lua.table_from([17]))
    vm, lua = s.vm, s.lua
    assert s.system["providers"]["$is_read"](vm, lua.table_from(["staffroom"])) == 0
    vm["load"](vm, "staffroom")
    assert s.system["providers"]["$is_read"](vm, lua.table_from(["folder/STAFFROOM.mjo"])) == 1
    op = {spec[1]: number for number, spec in vm["opcodes"].items()}
    key = 0x76520000
    fixture = lua.table_from({
        "name": "__persistent_fixture.mjo", "main": 0, "functions": [[key, 0]],
        "code": [[0, op["ldc.i"], 19], [6, op["stp.i"], 0, key, -1],
                 [16, op["ldstr"], "persistent fixture"],
                 [40, op["stp.s"], 512, key + 1, -1],
                 [50, op["ldc.i"], 0], [56, op["ret"]]]
    }, recursive=True)
    loader = vm["loader"]
    vm["loader"] = lambda name: fixture if name == "__persistent_fixture.mjo" else loader(name)
    vm["load"](vm, "__persistent_fixture")
    # Keep the artificial program outside the game script manifest used in
    # save files. Only this fixture's scalar results enter the real profile.
    t = vm["spawn"](vm, key, lua.table(), False)
    for _ in range(10):
        vm["run"](vm, 1000)
        if vm["threads"][t]["finished"]:
            break
    assert vm["globals"][0][key] == 19
    assert s.system["state"]["persist_dirty"]
    s.system["saves"]["flush"](vm)
    s.system["providers"]["$do_load"](vm, lua.table_from([17]))
    assert s.advance() == "text", s.summary()
    assert vm["globals"][0][key] == 19
    assert vm["globals"][0][key + 1] == "persistent fixture"
    assert s.system["providers"]["$is_read"](vm, lua.table_from(["staffroom"])) == 1
    s.system["state"]["persist_dirty"] = True
    s.system["saves"]["flush"](vm)
    restarted = Session(False, save_root=evidence / "saves")
    assert restarted.advance() == "ui", restarted.summary()
    assert restarted.vm["globals"][0][key] == 19
    assert restarted.vm["globals"][0][key + 1] == "persistent fixture"
    assert restarted.system["providers"]["$is_read"](restarted.vm, restarted.lua.table_from(["staffroom"])) == 1
    assert restarted.click(3) == "ui", restarted.summary()
    for identifier in (170, 180, 190, 130, 131):
        assert click_id(restarted, identifier) == "ui", restarted.summary()
    assert click_id(restarted, 999) == "ui", restarted.summary()
    report = {"status": "passed", "evidence": str(evidence),
              "older_slot_retains_process_variables": True,
              "cold_restart_before_library_init": True,
              "loaded_script_record_survives_slot_and_restart": True,
              "restarted_config_controls": [170, 180, 190, 130, 131],
              "scope": "independent int/string writes through VM; original title/config startup and old story slot"}
    write_json(ROOT / "reports/persistent_global_tests.json", report)
    print("persistent variables: old slot, cold restart and restarted config passed")
    return report


if __name__ == "__main__":
    run()
