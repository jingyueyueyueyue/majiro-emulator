"""Production missing-picture queries match the actual source EXE mode gates."""
import struct
import unicorn
from original_x86 import Original
from lua_bridge import create_lua, setup_runtime
from port_common import ROOT, write_json


def run():
    source = Original()
    values = []
    def pushed(uc, at, size, data):
        esp = uc.reg_read(source.reg.UC_X86_REG_ESP)
        ret, value = struct.unpack("<2I", source.read(esp,8))
        values.append(value)
        uc.reg_write(source.reg.UC_X86_REG_ESP, esp+4)
        uc.reg_write(source.reg.UC_X86_REG_EIP, ret)
    source.machine.hook_add(unicorn.UC_HOOK_CODE,pushed,begin=0x44eab0,end=0x44eab0)
    lua = create_lua()
    _,vm,system,_ = setup_runtime(lua,False)
    cases = []
    for name,address,flag in (("$is_report_no_pic",0x42fd80,0x55e20c),
                               ("$is_log_no_pic",0x42fdb0,0x55e208)):
        # mode 1 is production. Both enabled diagnostic flags are ignored.
        for enabled in (0,1):
            source.machine.mem_write(0x4c7ab8,struct.pack("<I",1))
            source.machine.mem_write(flag,struct.pack("<I",enabled))
            source.call(address,[])
            actual=system["providers"][name](vm,lua.table())
            assert actual==values[-1]==0
            cases.append({"name":name,"production":True,"diagnostic_flag":enabled,"result":actual})
    write_json(ROOT/"reports/missing_picture_flag_tests.json", {
        "status":"passed","source_exe_sha256":Original.IDENTITY,"cases":cases,
        "scope":"source script still decides fallback; no substitute image or success for missing prepared assets"})
    print("missing picture flags: 4 original production cases passed")


if __name__ == "__main__":
    run()
