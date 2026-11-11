"""Cross-build text, controls and proc comparisons through the real VM step."""
from __future__ import annotations

from collections import Counter
import hashlib
import itertools
import json
from pathlib import Path
import sys
import zlib

from audit_player_semantics import registered_providers,source_identity
from lua_bridge import table_value
from original_opcode import f32_bits,native_number
from original_text import TextOracle

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/"convert"))
from port_common import ROOT,archive_dir,timestamp,write_json


def crc_event_name(target):
    """A valid CP932 fixture alias exercises unnamed native hash delivery."""
    for prefix in range(256):
        head=f"FIXTURE_{prefix}_".encode()
        zero=zlib.crc32(head+bytes(4))
        basis={}
        for bit in range(32):
            raw=(1<<bit).to_bytes(4,"little")
            vector=zlib.crc32(head+raw)^zero;mask=1<<bit
            while vector:
                pivot=vector.bit_length()-1
                if pivot not in basis:
                    basis[pivot]=(vector,mask);break
                other,solution=basis[pivot];vector^=other;mask^=solution
        vector=target^zero;mask=0
        while vector:
            pivot=vector.bit_length()-1
            other,solution=basis[pivot];vector^=other;mask^=solution
        raw=head+mask.to_bytes(4,"little")
        try:text=raw.decode("cp932")
        except UnicodeError:continue
        if all(byte>=32 for byte in raw)and text.encode("cp932")==raw:
            assert zlib.crc32(raw)==target
            return text
    raise ValueError("no bounded CP932 fixture event alias")


EVENTS={name:zlib.crc32(name.encode())for name in
        ("PAUSE","CONSOLE_CLS","CRLF","CONSOLE_WROTE","NAME_DISP","X_CONTROL",
         "GAIZI_OUT","CONSOLE_ON","CONSOLE_OFF")}
EVENTS[crc_event_name(0x7ee67053)]=0x7ee67053


def text(value):return("text",value,(),())
def ctrl(value,*args,tags=()):return("ctrl",value,args,tags)
def proc():return("proc",None,(),())
def api(name,*args):return("api",name,args,())


def programs():
    for value in("","ABC","\u3042A\uff76","A|B","A\u203bB","\u300cABC\u300d"):
        yield"text_buffer",[text(value)]
        yield"text_append",[text("A"),text(value),text("B")]
        yield"proc_short",[text(value),proc(),proc()]
    for tag,values in((0,(-2147483648,-1,0,1,2147483647)),
                      (1,(-1.25,-0.0,0.0,1.0,1.25,123456.25)),
                      (2,("","ABC","\u3042"))):
        for value in values:
            yield"typed_display",[ctrl("d",value,tags=(tag,))]
            yield"typed_display_append",[text("A"),ctrl("d",value,tags=(tag,)),text("B")]
    for command in("p","P","n","N","z","w"):
        yield"control_"+command,[ctrl("z"),ctrl("s",13),ctrl(command)]
        yield"body_"+command,[text("ABC"),proc(),ctrl(command),text("DEF"),proc()]
    for value in(-2,-1,0,1,20,2147483647):
        yield"speed",[ctrl("s",value)]
    for milliseconds in(-20,-1,0,1,100,2000):
        yield"timed_wait",[ctrl("t",milliseconds)]
        yield"zero_timed_wait",[ctrl("z"),ctrl("t",milliseconds)]
    for x,y in((0,0),(5,7),(-5,13),(200,-20)):
        yield"locate",[ctrl("l",x,y)]
        yield"offset_restore",[ctrl("o",x,y),ctrl("r")]
        yield"offset_style",[ctrl("o",x,y),ctrl("c",0x556677,-1),
                             ctrl("f",32,15,40,"",1),ctrl("z"),ctrl("s",3),ctrl("r")]
    for values in((50,-1,-1,"",-1),(30,11,42,"",1),(-1,-1,-1,"",-1),
                  (19,-1,30,"",0),(32,16,40,"MS Mincho",1)):
        yield"font",[ctrl("f",*values)]
    for color,shadow in((0,-1),(0xffffff,0),(0x112233,0x445566)):
        yield"color",[ctrl("c",color,shadow)]
    for value in("","ABC","\u3042"):yield"x_event",[ctrl("x",value)]
    for mask in range(64):
        args=tuple(index+1 if mask&(1<<index)else -99 for index in range(6))
        yield"gaizi",[ctrl("g",*args)]
    for count in(1,2,3):
        commands=[api("$histbuff_disable")]*count+[api("$histbuff_enable")]*count
        yield"history_depth",commands
        yield"filtered_message",commands[:count]+[text("A"),proc()]+commands[count:]+[text("B"),proc()]
    yield"negative_history_depth",[api("$histbuff_enable"),text("A"),proc(),
        api("$histbuff_disable"),text("B"),proc()]
    yield"overlay_message",[text("A"),proc(),ctrl("o",5,7),text("B"),proc(),
        ctrl("r"),text("C"),proc()]
    yield"color_default",[api("$console_color",0x123456)]
    for args in(("MS Gothic",),("",30),("",30,-1),("",30,14,42),
                 ("MS Mincho",30,14,42,1)):
        yield"console_font",[api("$console_font",*args)]
    yield"console_queries",[text("ABC"),proc()]+[api(name)for name in
        ("$get_last_msg$","$console_curpos_x","$console_curpos_y",
         "$console_font_height_get","$console_is_on")]
    for y in(49,50,51,100):
        yield"page_overflow",[api("$console_set",1,20,30,120,40),
            api("$console_locate",20,y),text("ABC"),proc(),proc()]
    yield"page_wrap_overflow",[api("$console_set",1,20,30,40,20),
        text("ABCDEFGHI"),proc(),proc(),proc(),proc(),proc()]
    for value in("ABCDEFGHIJKLMNOPQ","\u3042\u3044\u3046\u3048\u304a\u304b\u304d",
                  "A\u3042B\u3044C\u3046D\u3048E"):
        yield"wrap",[text(value),proc(),proc(),proc()]


FIXTURE=r"""
function(mod,ops,system,profile,events)
  local script={name='text-fixture',main=0,functions={},code={{0,0x840},{10000,0x191}}}
  local vm=mod.new{opcodes=ops,loader=function()return script end,
    syscall=system.invoke,text_ops=system.text,string_ops=system.strings,numeric_profile=profile}
  vm:begin(script.name)
  local recorded,draws={},{}
  for name,hash in pairs(events)do
    system.state.events[name]={{script='event-fixture',offset=hash}}
  end
  vm.resume=function(self,action)
    assert(action.kind=='sequence','unexpected text fixture transport')
    for _,call in ipairs(action.calls)do
      local args={}
      for i,value in ipairs(call.args)do args[i]=value end
      if call.offset==0x47bb540c then args[1]=1 end
      recorded[#recorded+1]={hash=string.format('%08x',call.offset),args=args,
        retry=self:frame().index==1}
    end
    if action.done and action.done.wants then self:push(0,0)end
  end
  local function state()
    local c=system.state.console
    return {buffer=system.state.text_buffer,x=c.x,y=c.y,font=c.font_spec,
      color=c.color,shadow=c.shadow,zero=c.zero_time,speed=c.text_speed,
      overlay=c.overlay_active or false,history_depth=c.history_depth or 0,visible=c.visible}
  end
  return {vm=vm,state=state,events=recorded,
    step=function(name,value,args,tags,hash)
      local row={0,({text=0x840,proc=0x841,ctrl=0x842})[name],value}
      if name=='api'then row={0,0x834,hash,#args}end
      script.code[1]=row;vm:frame().index=1
      if name=='api'then
        for i=#args,1,-1 do vm:push(args[i],tags[i])end
      else for i,arg in ipairs(args)do vm:push(arg,tags[i])end end
      vm:step()
      local stack={}
      for i,item in ipairs(vm.stack)do stack[i]=item end
      if name=='api'then vm:pop()end
      return {state=state(),retry=vm:frame().index==1,stack=stack,events=recorded,
        message=system.providers['$get_last_msg$'](vm,{}),wait=vm.waiting and vm.waiting.until_ms or 0}
    end}
end
"""


def run():
    samples=json.loads((ROOT/"reports/opcode_dispatch.json").read_text(encoding="utf-8"))["samples"]
    dispatch=json.loads((ROOT/"reports/syscall_dispatch.json").read_text(encoding="utf-8"))["samples"]
    by_name={row["sample"]:row for row in dispatch}
    profiles=json.loads((ROOT/"config/player_engine_profiles.json").read_text(encoding="utf-8"))["executables"]
    before=source_identity()
    report={"schema":1,"timestamp":timestamp(),"source_identity":before,
        "vm_sha256":before["files"]["player/runtime/majiro_vm.lua"],"samples":[],"failures":[],
        "scope":"horizontal CP932 grid metrics; typed text formatting, used controls, filtered current message, nested history, style offsets, proc retry and basic wrapping",
        "host_boundaries":["MSVCRT formatting","deterministic font metrics","glyph raster output",
                           "empty event transport","opaque valid page","acknowledged page-overflow warning"],
        "limits":["font-specific raster/kerning, kinsoku options, vertical layout, voice continuation/ducking and raw API variants, history replay and executing event callbacks require further validation"],
        "pending_features":["font_metrics_and_raster","kinsoku_and_indentation","vertical_layout",
                            "voice_continuation_panning_and_ducking","voice_raw_wrapper_variants",
                            "history_replay","executing_event_callbacks"],
        "complete_engine_semantics":False}
    for sample in samples:
        oracle=TextOracle(sample,by_name[sample["sample"]]["entries"])
        counts,failures,groups=Counter(),Counter(),Counter()
        call_counts,call_failures,call_arities=Counter(),Counter(),{}
        profile=profiles[sample["exe_sha256"]]
        assert profile["text_controls"]==oracle.profile()
        for group,program in programs():
            oracle.configure()
            draws=[]
            def graphics(request):
                if not request.startswith("glyphs "):return"OK"
                header,face,steps,body=request.split("\n",3)
                fields=list(map(int,header.split()[1:]));width=sum(map(int,steps.split()))
                draws.append({"text":body,"x":fields[1],"y":fields[2],"width":width,"height":fields[3]})
                return"OK"
            lua,_,_,system=registered_providers(overrides={
                "text_controls":profile["text_controls"],"graphics":graphics,"now":lambda:100})
            mod=lua.execute((ROOT/"player/runtime/majiro_vm.lua").read_text(encoding="utf-8"))
            ops=lua.execute((ROOT/"player/tables/opcodes.lua").read_text(encoding="utf-8"))
            c=system["state"]["console"]
            c["configuration"]=lua.table_from([1,20,30,120,300])
            c["font_spec"]=lua.table_from(["MS Gothic",20,10,24,0])
            c["visible"]=True;c["x"],c["y"]=20,30;c["color"],c["shadow"]=0x112233,0x445566
            c["overlay_active"]=False
            fixture=lua.eval(FIXTURE)(mod,ops,system,profile["numeric"],lua.table_from(EVENTS))
            for step,(kind,value,args,tags)in enumerate(program):
                groups[group]+=1
                counts[kind]+=kind!="api"
                if kind=="api":
                    call_counts[value]+=1;call_arities.setdefault(value,set()).add(len(args))
                reason=None;expected=None;actual=None
                try:
                    values=[(2,oracle.string(arg,index))if isinstance(arg,str)else
                        (1,f32_bits(arg))if(tags and tags[index]==1)else(0,int(arg)&0xffffffff)
                        for index,arg in enumerate(args)]
                    if kind=="api":
                        native_args=list(args)
                        if value=="$console_set":native_args[0]=oracle.page
                        native=oracle.call_api(value,native_args)
                        expected={"state":oracle.text_state(),"pc":1,"stack":native}
                        hash_=int(oracle.entries[value]["hash"],16)
                    else:
                        expected=oracle.instruction(kind,value,values);hash_=None
                    actual=table_value(fixture["step"](kind,value,lua.table_from(args),lua.table_from(tags),hash_))
                    actual["stack"]=actual["stack"]or[]
                    actual["events"]=actual["events"]or[]
                    for event in actual["events"]:event["args"]=event["args"]or[]
                    if expected["state"]!=actual["state"]:
                        changed={key:(actual["state"].get(key),item)for key,item in expected["state"].items()
                                 if actual["state"].get(key)!=item}
                        reason=f"state {changed}"
                    elif bool(expected["pc"]==0)!=actual["retry"]:reason="parent instruction retry differs"
                    if not reason and kind=="ctrl"and value=="t"and expected["wait"]!=actual["wait"]:
                        reason=f"time wait {actual['wait']} != {expected['wait']}"
                    expected_stack=[oracle.string_result(tag,raw)if tag==2 else native_number(tag,raw)
                                    for tag,raw in expected["stack"]]
                    if not reason and expected_stack!=actual["stack"]:reason=f"stack {actual['stack']} != {expected_stack}"
                    native_events=[]
                    for event in oracle.events:
                        event=dict(event);event["args"]=list(event["args"])
                        if event["hash"]=="47bb540c":event["args"][0]=1
                        event["retry"]=event.pop("pc")==0
                        native_events.append(event)
                    if not reason and native_events!=actual["events"]:reason=f"events {actual['events']} != {native_events}"
                    if not reason and oracle.draws!=draws:reason=f"draws {draws} != {oracle.draws}"
                    message=oracle.last_message()
                    if not reason and message!=actual["message"]:reason=f"message {actual['message']!r} != {message!r}"
                except Exception as error:reason=repr(error)
                if reason:
                    (call_failures if kind=="api"else failures)[value if kind=="api"else kind]+=1
                    if len(report["failures"])<90:
                        report["failures"].append({"sample":sample["sample"],"group":group,"step":step,
                            "kind":kind,"value":value,"args":args,"tags":tags,"reason":reason})
                    break
        calls=[{"hash":oracle.entries[name]["hash"],"name":name,"cases":count,
                "failures":call_failures[name],"status":"passed"if not call_failures[name]else"failed",
                "argument_counts":sorted(call_arities[name]),"discard_modes":[False]}
               for name,count in sorted(call_counts.items())]
        cases={name:count for name,count in counts.items()if name!="api"}
        report["samples"].append({"sample":sample["sample"],"exe_sha256":sample["exe_sha256"],
            "cases":cases,"failures":dict(failures),"calls":calls,"groups":dict(groups),
            "passed_opcodes":[name for name in cases if not failures[name]],
            "text_profile":profile["text_controls"]})
        print(f"{sample['sample']}: {sum(cases.values())+sum(call_counts.values())} text steps; "
              f"{sum(failures.values())+sum(call_failures.values())} mismatches",flush=True)
    report["comparisons"]=sum(sum(row["cases"].values())+sum(call["cases"]for call in row["calls"])
        for row in report["samples"])
    report["mismatches"]=sum(sum(row["failures"].values())+sum(call["failures"]for call in row["calls"])
        for row in report["samples"])
    report["test_sources"]={name:hashlib.sha256((ROOT/"scripts/analysis"/name).read_bytes()).hexdigest()
        for name in("original_opcode.py","original_strings.py","original_calls.py","native_function.py",
                    "original_text.py","test_text_semantics.py")}
    report["source_changed_during_test"]=source_identity()!=before
    report["status"]="passed"if not report["mismatches"]and not report["source_changed_during_test"]else"failed"
    folder=archive_dir("text_semantics")
    write_json(folder/(folder.name[:8]+"_text_semantics.json"),report)
    write_json(ROOT/"reports/text_semantics_tests.json",report)
    return 0 if report["status"]=="passed"else 1


if __name__=="__main__":raise SystemExit(run())
