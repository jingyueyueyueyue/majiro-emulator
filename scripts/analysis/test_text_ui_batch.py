"""Focused entry checks for console/font/output and editable Android dialogs."""
from __future__ import annotations
from player_paths import player_path
import json
from pathlib import Path
import sys

from audit_player_semantics import registered_providers
from native_pixels import Pixels

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/"convert"))
from port_common import ROOT,archive_dir,timestamp,write_json


def run():
    folder=archive_dir("text_ui_batch");host={"result":"pending","request":None};logs=[];fonts=[]
    def dialog(action,token,flags,message,caption):
        if action=="begin":host["request"]=(token,flags,message,caption);return"pending"
        return host["result"]
    def output(*args):logs.append(args)
    def font(name):fonts.append(name);return True
    pixels=Pixels()
    lua,_,_,system=registered_providers(overrides={"graphics":pixels,"dialog":dialog,
        "testplay":True,"debug_output":output,"load_font":font})
    module=lambda name:lua.execute(player_path(name).read_text(encoding="utf8"))
    vm=lua.eval("""function(mod,ops,system)
      local script={name='text-ui-fixture.mjo',source_sha256=string.rep('a',64),main=0,
        functions={},code={{0,0x82c,0}}}
      local vm=mod.new{opcodes=ops,loader=function()return script end,
        syscall=system.invoke,resume_action=system.resume_action,string_ops=system.strings}
      vm:begin(script.name);return vm
    end""")(module("runtime/majiro_vm.lua"),module("resources/scripts/opcodes.lua"),system)
    def call(name,*args):return system["providers"][name](vm,lua.table_from(args,recursive=True),True)
    call("$load_font","sample.ttf");assert fonts==["sample.ttf"]
    call("$debugout","message",7);assert logs[-1][0]=="message 7 "
    call("$console_setzerotime");assert system["state"]["console"]["zero_time"]
    call("$console_resetzerotime");assert not system["state"]["console"]["zero_time"]
    call("$dialog_input$","Prompt","Draft")
    assert host["request"][1:]==(65537,"Prompt","Draft")
    assert vm["run"](vm,1)=="waiting"
    host["result"]="data "+b"edited".hex();system["poll"](vm,lua.table())
    assert vm["pop"](vm)=="edited"
    for name,flag in(("$all_setup_dialog",131073),("$volume_setup_dialog",196609),
                     ("$speed_setup_dialog",262145)):
        host["result"]="pending";call(name)
        assert host["request"][1]==flag
        data="musicvolume=543"if name!="$speed_setup_dialog"else"mojispeed=1234"
        host["result"]="data "+data.encode("utf8").hex();system["poll"](vm,lua.table())
        assert vm["pop"](vm)==0
        field,value=data.split("=");assert system["state"]["settings"][field]==int(value)
    page=call("$page_create",8,4)
    call("$console_set",page,0,0,8,4);call("$console_font","sans-serif",2,1,2,0)
    console=system["state"]["console"]
    console["history"]=lua.table_from([{"text":"AB","name":"","page":[{"text":"AB","x":0,"y":0}],
        "bytes":2,"x":2,"y":0,"configuration":[page,0,0,8,4],"font_spec":["sans-serif",2,1,2,0]}],
        recursive=True)
    console["history_bytes"]=2
    call("$console_replay",-1)
    assert console["text"]=="AB"and console["visible"]
    call("$console_dispose");assert len(console["history"])==0
    call("$grp_zoom",.008)
    request=system["screen"]["present"](page,str(folder/"20261110_zoom.png"),"")
    assert request.startswith("presentzoom ")
    assert pixels(request).startswith("OK")
    report={"schema":1,"timestamp":timestamp(),"status":"passed",
        "entries":["load_font","debugout","console_setzerotime","console_resetzerotime","dialog_input$",
                   "all_setup_dialog","volume_setup_dialog","speed_setup_dialog","console_replay",
                   "console_dispose","grp_zoom"],
        "scope":"new provider entry, asynchronous editor/settings return, raster/zoom transport",
        "limits":["Android physical UI, full original history stream and OS font registration remain final acceptance targets"]}
    write_json(folder/(folder.name[:8]+"_text_ui_batch.json"),report)
    write_json(ROOT/"reports/text_ui_batch_tests.json",report)
    print("Text/UI batch entry checks passed")


if __name__=="__main__":run()
