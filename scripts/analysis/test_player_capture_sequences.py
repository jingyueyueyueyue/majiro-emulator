"""Execute original mouse message/dispatch code against player callback traces."""
from pathlib import Path
import struct
import sys

import unicorn
from original_x86 import Original
from audit_player_semantics import registered_providers

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/"convert"))
from port_common import ROOT,archive_dir,timestamp,write_json


class CaptureOracle(Original):
    def __init__(self):
        super().__init__()
        self.now=1000;self.x=12;self.y=34;self.events=[];self.keys=set()
        self.hooks=[]
        def boundary(at,callback,words=0):
            def invoke(uc,address,size,unused):
                sp=uc.reg_read(self.reg.UC_X86_REG_ESP)
                value=callback(sp)
                target=self.read32(sp)
                uc.reg_write(self.reg.UC_X86_REG_EAX,int(value or 0)&0xffffffff)
                uc.reg_write(self.reg.UC_X86_REG_ESP,sp+4+words*4)
                uc.reg_write(self.reg.UC_X86_REG_EIP,target)
            self.hooks.append(self.machine.hook_add(unicorn.UC_HOOK_CODE,invoke,begin=at,end=at))
        frame=self.memory(bytes(32));self.put(0x5d1a58,frame)
        boundary(0x44eab0,lambda sp:0)
        boundary(0x438bf0,lambda sp:0)
        boundary(0x4635b0,lambda sp:0)  # Win32 capture/clip boundary
        boundary(0x473dd0,lambda sp:1)
        for address in(0x475640,0x475670):
            boundary(address,lambda sp:self.read32(sp+4))
        boundary(0x44bc80,lambda sp:int(self.read32(sp+4)in self.keys))
        def callback(sp):
            hash_,count=self.read32(sp+4),self.read32(sp+8)
            if hash_:
                self.events.append((hash_,[self.signed(self.read32(sp+12+i*4))for i in range(count)]))
            return 0
        boundary(0x4634d0,callback)
        def cursor(sp):
            self.machine.mem_write(self.read32(sp+4),struct.pack("<ii",self.x,self.y))
            return 1
        for index,(slot,fn,argc)in enumerate([
            (0x4a93ec,cursor,1),(0x4a93e8,lambda sp:1,2),
            (0x4a9420,lambda sp:self.now,0),(0x4a93d0,lambda sp:1,0),
            (0x4a9330,lambda sp:1,1)]):
            target=self.STOP+0x100+index*16;self.put(slot,target);boundary(target,fn,argc)
        self.machine.mem_write(0x5b4320,bytes(0x100))
        self.put(0x5b4310,1)
        self.put(0x5b4320,self.x);self.put(0x5b4324,self.y)
        self.put(0x5b4380,self.x-999);self.put(0x5b4384,self.y-999)
        for at,value in[(0x5b437c,1),(0x5b43dc,2),(0x5b43e0,3)]:
            self.put(at,value)
        for i in range(14):self.put(0x5b43e4+i*4,10+i)

    @staticmethod
    def signed(value):return(value+0x80000000)%0x100000000-0x80000000
    def read32(self,at):return int.from_bytes(self.read(at,4),"little")
    def put(self,at,value):self.machine.mem_write(at,struct.pack("<I",value&0xffffffff))
    def step(self,time,x,y,message=None,keys=()):
        self.now,self.x,self.y=time,x,y;self.keys=set(keys);self.events=[]
        if message:self.call(0x463690,[1,message,0,((y&65535)<<16)|(x&65535)],count=200000)
        self.call(0x4639e0,[0,0],count=500000)
        return self.events[:]


def run():
    scenarios={
        "single_click":[(1000,12,34,0x201),(1050,12,34,0x202),(1100,12,34,None),
                        (1250,12,34,None),(1251,12,34,None)],
        "double_click":[(1000,12,34,0x201),(1050,12,34,0x202),(1060,12,34,None),
                        (1100,12,34,0x203),(1150,12,34,0x202),(1160,12,34,None),(1400,12,34,None)],
        "drag_by_motion":[(1000,12,34,0x201),(1010,21,34,None),(1020,25,36,None),
                          (1030,25,36,0x202),(1040,25,36,None)],
        "drag_by_hold":[(1000,12,34,0x201),(1250,12,34,None),(1251,12,34,None),
                        (1260,12,34,0x202),(1270,12,34,None)],
        "cancel_drag":[(1000,12,34,0x201),(1010,22,34,None),(1020,22,34,0x204),
                       (1030,22,34,None)],
        "right_click":[(1000,12,34,0x204),(1010,12,34,0x205),(1260,12,34,None)],
        "nested_end_pending_click":[(1000,12,34,0x201),(1010,12,34,None),
                                    (1020,12,34,0x202),(1251,12,34,None)],
        "nested_end_drag":[(1000,12,34,0x201),(1010,22,34,None),(1020,24,34,None),
                           (1030,24,34,0x202),(1040,24,34,None)],
    }
    mirror={0x201:0x204,0x202:0x205,0x203:0x206,0x204:0x201,0x205:0x202,0x206:0x203}
    for label in("double_click","drag_by_motion","drag_by_hold","cancel_drag"):
        scenarios["right_"+label]=[(time,x,y,mirror.get(message,message))
                                  for time,x,y,message in scenarios[label]]
    rows=[];failures=[]
    for label,steps in scenarios.items():
        original=CaptureOracle();clock=[1000]
        lua,_,_,system=registered_providers(overrides={"now":lambda:clock[0],"wall_now":lambda:clock[0]})
        vm=lua.eval("""function()return{
          resolve=function(self,h)return{script={name='capture'},offset=h}end,
          resume=function(self,a)self.last=a end}end""")()
        call=lambda name,*args:system["providers"][name](vm,lua.table_from(args),False)
        system["input"]["sample"](lua.table_from({"x":12,"y":34,"held_buttons":0}))
        call("$mouse_capture_init",1,2,3)
        if label.startswith("nested_end"):
            original.put(0x5b4310,2)
            call("$mouse_capture_init",1,2,3)
        call("$set_mouse_capture_left",*range(10,17))
        call("$set_mouse_capture_right",*range(17,24))
        held=0;observed=[]
        for time,x,y,message in steps:
            if(label=="nested_end_pending_click"and time==1010)or(label=="nested_end_drag"and time==1020):
                original.call(0x463eb0,[])
                vm["thread_id"]=2
                call("$mouse_capture_end")
            if message in(0x201,0x203):held|=1
            elif message==0x202:held&=~1
            elif message in(0x204,0x206):held|=2
            elif message==0x205:held&=~2
            expected=original.step(time,x,y,message)
            clock[0]=time
            system["input"]["sample"](lua.table_from({"x":x,"y":y,"held_buttons":held}))
            call("$do_mouse_capture")
            actual=[(row["offset"],list(row["args"].values()))for row in vm["last"]["calls"].values()]
            observed.append({"time":time,"message":message,"expected":expected,"actual":actual})
            if actual!=expected:failures.append({"case":label,**observed[-1]})
        rows.append({"case":label,"steps":observed})
    report={"status":"failed"if failures else"passed","timestamp":timestamp(),"cases":rows,
            "comparisons":sum(len(row["steps"])for row in rows),"failures":failures,
            "original_exe_sha256":Original.IDENTITY,
            "boundaries":["OS cursor/capture/time and logical coordinate conversion","script callback transport"],
            "scope":"actual original message processor and polling dispatcher; single/double/drag/cancel traces"}
    folder=archive_dir("capture_sequences")
    write_json(folder/"report.json",report)
    write_json(ROOT/"reports/player_capture_sequence_tests.json",report)
    print(report["status"],len(failures),failures[:3],flush=True)
    return report


if __name__=="__main__":
    raise SystemExit(run()["status"]!="passed")
