"""Original fast-mode read limits and interruption of existing main-script waits."""
import struct
import unicorn
from original_x86 import Original
from lua_bridge import create_lua, setup_runtime
from port_common import ROOT, write_json


def keyboard(o, control):
    def hook(uc, at, size, data):
        esp = uc.reg_read(o.reg.UC_X86_REG_ESP)
        target = struct.unpack("<I", o.read(esp, 4))[0]
        uc.reg_write(o.reg.UC_X86_REG_EAX, int(control) if at == 0x44bba0 else 0)
        uc.reg_write(o.reg.UC_X86_REG_ESP, esp + 4)
        uc.reg_write(o.reg.UC_X86_REG_EIP, target)
    for at in (0x44bba0, 0x44bbf0, 0x44bc30):
        o.machine.hook_add(unicorn.UC_HOOK_CODE, hook, begin=at, end=at)


def run():
    lua = create_lua()
    _, vm, system, clock = setup_runtime(lua, False)
    state = system["state"]
    settings = state["settings"]
    queries = []
    for config in (0, 1):
        for control in (False, True):
            for read in (False, True):
                for clicked in (False, True):
                    o = Original(); keyboard(o, control)
                    for at, value in ((0x4dc344, config), (0x4dc364, int(read)),
                                      (0x4dc35c, int(clicked and config == 0)),
                                      (0x4dc360, int(clicked and config == 1))):
                        o.machine.mem_write(at, struct.pack("<I", value))
                    settings["fast_allowed"] = True
                    settings["fast_mode"] = clicked and config == 0
                    settings["fast_read_mode"] = clicked and config == 1
                    settings["ultra_fast_mode"] = False
                    settings["auto_mode"] = False
                    settings["fast_mode_config"] = config
                    state["current_text_read"] = read
                    state["input"]["down"] = lua.table_from({17: control})
                    expected = o.call(0x43a950, [])
                    actual = system["providers"]["$is_fast_mode"](vm, lua.table())
                    assert actual == expected, (config, control, read, clicked, actual, expected)
                    original_read_mode = struct.unpack("<I", o.read(0x4dc360, 4))[0]
                    assert bool(settings["fast_read_mode"]) == bool(original_read_mode)
                    queries.append({"config": config, "control": control, "read": read,
                                    "button": clicked, "fast": actual})
    waits = []
    for thread_type in (0, 1):
        for fast in (False, True):
            for timeout in (4000, -1):
                o = Original(); keyboard(o, False)
                context = o.memory(bytes(0x5100))
                o.machine.mem_write(0x5d1a5c, struct.pack("<I", context))
                o.machine.mem_write(0x5d1a58, struct.pack("<I", context + 0x28))
                o.machine.mem_write(context + 4, struct.pack("<I", thread_type))
                o.machine.mem_write(context + 0x70, struct.pack("<i", timeout))
                o.machine.mem_write(0x4dc35c, struct.pack("<I", int(fast)))
                original_pending = o.syscall(0x4424c0, [])
                assert original_pending == int(thread_type != 0 or not fast)
                settings["fast_allowed"] = True
                settings["fast_mode"] = fast
                settings["fast_read_mode"] = False
                settings["ultra_fast_mode"] = False
                state["input"]["down"] = lua.table()
                if thread_type:
                    vm["threads"][2] = lua.table_from({
                        "id": 2, "frames": [], "stack": [], "globals": {}}, recursive=True)
                    vm["thread_order"][2] = 2
                    vm["select_thread"](vm, 2)
                system["providers"]["$wait"](vm, lua.table_from([timeout]))
                vm["save_thread"](vm)
                vm["select_thread"](vm, 1)
                system["poll"](vm, lua.table())
                pending = bool(vm["threads"][2 if thread_type else 1]["waiting"])
                assert pending == bool(original_pending), (thread_type, fast, timeout, pending)
                vm["threads"][2 if thread_type else 1]["waiting"] = None
                vm["waiting"] = None
                if thread_type:
                    vm["threads"][2]["finished"] = True
                waits.append({"thread_type": thread_type, "fast": fast,
                              "wait": timeout, "pending_after_next_poll": pending})
    # Frame synchronization is a separate source operation, not an interruptible wait.
    settings["fast_mode"] = True
    system["providers"]["$timer_sync"](vm, lua.table_from([0, 200]))
    system["poll"](vm, lua.table())
    assert vm["waiting"] and vm["waiting"]["until_ms"] == 200
    jumps = []
    for target in (500, 2000):
        o = Original()
        context = o.memory(bytes(0x5100))
        owner = o.memory(struct.pack("<I", context))
        locals_base = o.memory(bytes(256)) + 128
        o.machine.mem_write(locals_base-4, struct.pack("<I", owner))
        o.machine.mem_write(context+0x28, struct.pack("<I", 1000))
        o.machine.mem_write(context+0x3c, struct.pack("<I", target))
        o.machine.mem_write(context+0x70, struct.pack("<I", 4000))
        o.machine.mem_write(0x4dc35c, struct.pack("<I", 1))
        def setup(uc, at, size, data):
            for register, value in ((o.reg.UC_X86_REG_EAX, 0),
                (o.reg.UC_X86_REG_ESI, context), (o.reg.UC_X86_REG_EDI, 0),
                (o.reg.UC_X86_REG_ECX, owner), (o.reg.UC_X86_REG_EDX, 0),
                (o.reg.UC_X86_REG_EBP, locals_base)):
                uc.reg_write(register, value)
        def stop(uc, at, size, data):
            uc.reg_write(o.reg.UC_X86_REG_EIP, o.STOP)
        o.machine.hook_add(unicorn.UC_HOOK_CODE, setup, begin=0x439273, end=0x439273)
        o.machine.hook_add(unicorn.UC_HOOK_CODE, stop, begin=0x4392dc, end=0x4392dc)
        o.call(0x439273, [])
        actual = struct.unpack("<I", o.read(context+0x28, 4))[0]
        assert actual == (target if target > 1000 else 1000)
        # An independent suspended VM frame exercises the same forward rule.
        lua2=create_lua()
        _, v, sy, _=setup_runtime(lua2, False)
        op={spec[1]: number for number,spec in v["opcodes"].items()}
        fixture=lua2.table_from({"name":"__fast_wait.mjo","main":0,"functions":[],
            "code":[[0,op["ldc.i"],0],[10,op["ret"]],
                    [500,op["ldc.i"],0],[1000,op["ldc.i"],0],[2000,op["ret"]]]},recursive=True)
        v["loader"]=lambda name:fixture
        v["restart_on_return"]=False
        v["begin"](v,"__fast_wait")
        frame=v["frames"][1]
        frame["index"]=4
        frame["handlers"]=lua2.table_from([{"kind":"left","offset":target}],recursive=True)
        v["waiting"]=lua2.table_from({"kind":"time","until_ms":4000,"fast_interrupt":True})
        sy["state"]["settings"]["fast_mode"]=True
        sy["poll"](v,lua2.table())
        pending=v["threads"][1]["pending_input"]
        assert bool(pending)==(target>1000)
        jumps.append({"target":target,"current_pc":1000,"original_after":actual,
                      "runtime_forward_selection":bool(pending)})
    report = {"status": "passed", "source_exe_sha256": Original.IDENTITY,
              "queries": queries, "waits": waits, "timer_sync_preserved": True,
              "scheduler_fast_selection":jumps,
              "scope": "original x86 fast/read query and existing-wait resumption; keyboard boundaries stubbed"}
    write_json(ROOT / "reports/fast_response_tests.json", report)
    print("fast response:", len(queries), "original mode queries,", len(waits), "wait resumptions passed")


if __name__ == "__main__":
    run()
