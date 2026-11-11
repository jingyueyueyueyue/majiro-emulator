"""Execute native caller-slot fallback for reads beyond supplied optional arguments."""
from pathlib import Path
import json
import struct
import sys
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/"convert"))
from lua_bridge import create_lua
from original_calls import CallOracle
from port_common import ROOT,archive_dir,read_json,write_json,timestamp


def main():
    lua=create_lua();module=lua.execute((ROOT/"player/runtime/majiro_vm.lua").read_text(encoding="utf8"))
    ops=lua.execute((ROOT/"player/tables/opcodes.lua").read_text(encoding="utf8"))
    execute=lua.eval("""function(mod,ops,prefix)
      local script={name='optional-slots',main=0,functions={{305419896,100}},
        code={{0,0x80f,305419896,0,2},{12,0x191},
          {100,0x802,96,99,-4},{110,0x82b}}}
      local vm=mod.new{opcodes=ops,loader=function()return script end,syscall=function()error('syscall')end}
      vm:begin(script.name)
      for _,value in ipairs(prefix)do vm:push(value,0)end
      vm:push(20,0);vm:push(10,0)
      vm:step();assert(#vm:frame().args==2)
      vm:step();vm:step()
      return vm.stack
    end""")
    cases=[]
    for sample in read_json(ROOT/"reports/opcode_dispatch.json")["samples"]:
        oracle=CallOracle(sample)
        for prefix in([], [71],[11,22]):
            initial=[(0,value)for value in prefix+[20,10]]
            def setup(source):
                base=source.DATA+0x34000
                body=struct.pack("<HHIhH",int(source.rows["ld"]["opcode"],16),96,99,-4,
                    int(source.rows["ret"]["opcode"],16))
                source.machine.mem_write(base+100,body)
                source.registry([{"code_base":base,"functions":[(0x12345678,100)]}])
            expected=oracle.execute("call",initial,struct.pack("<IIH",0x12345678,0,2),
                setup=setup,instructions=3,budget=100000)
            actual=list(execute(module,ops,lua.table_from(prefix)).values())
            assert [(0,value&0xffffffff)for value in actual]==expected,(sample["sample"],prefix,actual,expected)
            cases.append({"game":sample["sample"],"caller_slots":prefix,"result":actual,"status":"passed"})
    folder=archive_dir("optional_argument_slots")
    report={"status":"passed","timestamp":timestamp(),"cases":cases,
        "strict_argument_type_checks_retained":True,"device_accessed":False,
        "evidence":str(folder.relative_to(ROOT))}
    write_json(ROOT/"reports/optional_argument_slots_tests.json",report);write_json(folder/"report.json",report)
    print("Optional argument slots:",len(cases),"original x86 comparisons passed")
    return 0


if __name__=="__main__":raise SystemExit(main())
