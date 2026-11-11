"""Minimal changed-entry comparisons for recovered high-hash tile routines."""
from pathlib import Path
import hashlib,json,struct,sys
import unicorn
from unicorn import x86_const as reg

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/"convert"))
from port_common import ROOT,archive_dir,timestamp,write_json
from original_x86 import Original
from lua_bridge import create_lua


class OriginalTiles(Original):
    def __init__(self):
        super().__init__()
        self.top=self.DATA+0x8000
        self.result=0
        self.seed=1
        self.static_data=bytes(self.machine.mem_read(0x4A0000,0x30000))
        self.boundaries={0x45B510,0x45C8E0,0x44EC90,0x44EAB0,
                         0x48999C,0x489C60,0x48AA80,0x48B0D0,0x48A437,0x479370}
        def hook(uc,at,size,unused):
            esp=uc.reg_read(reg.UC_X86_REG_ESP);dest=self.read32(esp)
            eax=uc.reg_read(reg.UC_X86_REG_EAX)if at==0x48A437 else 0
            if at==0x44EC90:eax=self.top
            elif at==0x44EAB0:self.result=self.read32(esp+4)
            elif at==0x48999C:
                self.seed=(self.seed*214013+2531011)&0xffffffff
                eax=(self.seed>>16)&32767
                uc.reg_write(reg.UC_X86_REG_ECX,eax)
            elif at in(0x489C60,0x48AA80):
                output,source,count=[self.read32(esp+i*4)for i in(1,2,3)]
                if count>65536:raise ValueError("original tile copy bound")
                uc.mem_write(output,bytes(uc.mem_read(source,count)));eax=output
            elif at==0x48B0D0:
                output,value,count=[self.read32(esp+i*4)for i in(1,2,3)]
                if count>65536:raise ValueError("original tile fill bound")
                uc.mem_write(output,bytes([value&255])*count);eax=output
            elif at==0x479370:raise ValueError("original tile diagnostic")
            uc.reg_write(reg.UC_X86_REG_EAX,eax)
            uc.reg_write(reg.UC_X86_REG_EIP,dest);uc.reg_write(reg.UC_X86_REG_ESP,esp+4)
        for address in self.boundaries:self.machine.hook_add(unicorn.UC_HOOK_CODE,hook,begin=address,end=address)

    def read32(self,at):return struct.unpack("<I",self.machine.mem_read(at,4))[0]
    def execute(self,address,args,memory):
        self.machine.mem_write(0x4A0000,self.static_data)
        self.machine.mem_write(0x5B0000,bytes(0x23000));self.seed=1;self.result=0
        for at,value in memory.items():self.machine.mem_write(at,struct.pack("<I",value&0xffffffff))
        for index,value in enumerate(args):self.machine.mem_write(self.top-index*16+12,struct.pack("<I",value&0xffffffff))
        self.call(address,[len(args)],count=5000000)
        return self.result


def main():
    lua=create_lua()
    def module(name):
        path=ROOT/("player/"+name if name.startswith("runtime/")else name)
        return lua.execute(path.read_text(encoding="utf8"))
    providers=lua.table();state=lua.table()
    define=lua.eval("function(providers)return function(name,fn)providers[name]=fn end end")(providers)
    api=module("runtime/majiro_tiles_analysis.lua")["install"](define,state,lua.table_from({"modules":module}))
    program=api["program"];oracle=OriginalTiles()
    cases=[]
    def fixture():
        data={0x5D1578:4,0x5D1580:0,0x5D0798:0,0x5D0914:600}
        for i in range(150):data[0x5D1800+i*4]=10+(i%34)*10
        for kind in range(40):data[0x5B4480+kind*4]=4;data[0x5B4520+kind*4]=4
        for i in range(14):
            data[0x5D08E0+i*4]=[110,120,130,140,150,160,170,180,190,210,220,230,240,250][i]
            data[0x5D1280+i*4]=data[0x5D08E0+i*4]
            data[0x5D1020+i*4]=data[0x5D08E0+i*4]//10
            data[0x5D0F60+i*4]=data[0x5D08E0+i*4]+1
            data[0x5D0DE0+i*4]=0
        return data
    for hash_,args in (
        (0xEFD59130,[]),(0xBB9D7A7F,[0]),(0xEBB5886C,[3]),(0xEBB5886C,[0]),
        (0xFAF17B1C,[]),(0xDA60F137,[110]),(0xF5E5BE93,[]),
        (0xE5A2CE14,[3,170,16]),(0xD481E1A2,[3,110]),
        (0xAEFC64DA,[3]),(0xB758AC4E,[0]),(0xFB7FB744,[0]),
        (0xCA5C98F2,[0]),(0xBEF4EDFA,[0,110]),(0xD583668C,[]),
        (0xF0FD9886,[0]),(0xE316A8F2,[]),
        (0x0D36E2A7,[0]),(0x1CD5BCA8,[11]),(0x1F5A14D4,[]),
        (0x23D5D11D,[0]),(0x23D5D11D,[3]),(0x2C18D5A9,[]),
        (0x2E99E98C,[]),(0x45E32EA6,[0]),(0x50CD2DCA,[]),
        (0x5C9761C5,[]),(0x62442EC6,[]),(0x6DDEB30E,[3]),(0x850EECF4,[3,110])):
        memory=fixture()
        if hash_==0xDA60F137:memory[0x5D1580]=10
        if hash_ in(0xD481E1A2,0x850EECF4):
            for i,value in enumerate([110,111,112,130,140,150,160,170,180,190,210,220,230,113]):
                memory[0x5D08E0+i*4]=value
        if hash_==0xFAF17B1C:
            memory[0x5D1578]=2
        if hash_==0xBB9D7A7F:memory[0x5D0D90]=1
        if hash_ in(0xFB7FB744,0xCA5C98F2,0xBEF4EDFA,0xF0FD9886):
            memory[0x5D0D90]=1
            memory[0x5D1360]=-1
        if hash_==0xE316A8F2:
            memory[0x5D0A40]=0
        if hash_ in(0x45E32EA6,0x6DDEB30E):
            memory[0x5D1360]=-1;memory[0x5D07C0]=-1
        if hash_==0x2E99E98C:
            memory[0x4C7ECC]=-1
        expected=oracle.execute(program["entries"][hash_],args,memory)
        state["tiles"]["memory"]=lua.table_from(memory)
        state["random_seed"]=1
        actual=api["execute"](lua.table(),hash_,lua.table_from(args))
        assert (actual&0xffffffff)==expected,(f"{hash_:08x}",actual,expected)
        result=state["tiles"]["memory"]
        differences=[]
        for at in sorted(set(memory)|set(result.keys())):
            # Compare all changed source/algorithm state, including table
            # writes created by the portable routine.
            if at in memory or result[at]is not None:
                native=oracle.read32(at);portable=int(result[at]or 0)&0xffffffff
                if native!=portable:differences.append((hex(at),native,portable))
        assert not differences,(f"{hash_:08x}",differences[:10])
        assert state["random_seed"]==oracle.seed,(f"{hash_:08x}","RNG state")
        cases.append({"hash":f"{hash_:08x}","arguments":args,"status":"passed",
                      "operations":state["tiles"]["last_operation_count"]})
    report={"schema":1,"timestamp":timestamp(),"status":"passed","cases":cases,
            "exe_sha256":oracle.IDENTITY,"scope":"recovered high and lower tile entries with shared serializable numeric state",
            "test_sources":{name:hashlib.sha256((ROOT/name).read_bytes()).hexdigest()for name in(
                "player/runtime/majiro_tiles_analysis.lua","player/runtime/majiro_tile_program.lua",
                "scripts/analysis/test_tile_routines.py","scripts/convert/generate_tile_routines.py")},
            "full_scoring_validation":False}
    folder=archive_dir("tile_routine_tests")
    write_json(ROOT/"reports/tile_routine_tests.json",report)
    write_json(folder/"20261110_tile_routine_tests.json",report)
    print("recovered tiles:",len(cases),"original targeted entry checks passed")


if __name__=="__main__":main()
