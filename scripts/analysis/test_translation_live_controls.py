"""Exercise running-game mode policy and the production Lua adapter lifecycle."""
from pathlib import Path
import json
import subprocess
import sys
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/"convert"))
from lua_bridge import create_lua
from port_common import ROOT,archive_dir,read_json,sha256,timestamp,write_json


def main():
    folder=archive_dir("translation_live_controls")
    policy=ROOT/"player/android/src/org/majiro/player/TranslationModePolicy.java"
    fixture=folder/"LivePolicyChecks.java"
    fixture.write_text("""package org.majiro.player;public class LivePolicyChecks{
 public static void main(String[]args){
  int checks=0;
  for(String running:new String[]{"original","online","local"}){
   for(String requested:new String[]{"original","online","local","auto"}){
    boolean expected=running.equals(requested)||(!running.equals("local")&&
      (requested.equals("online")||requested.equals("original")));
    if(TranslationModePolicy.canApply(running,requested)!=expected)throw new AssertionError();
    checks++;
   }
  }
  System.out.print(checks);
 }
}""",encoding="utf8")
    java=Path(read_json(ROOT/"reports/android_toolchain.json")["java"])/"bin"
    def run(name,args):
        result=subprocess.run(args,cwd=folder,capture_output=True,timeout=30)
        (folder/(name+".txt")).write_bytes(result.stdout+result.stderr)
        if result.returncode:raise RuntimeError(name+" failed: "+str(folder/(name+".txt")))
        return result.stdout
    run("compile",[str(java/"javac.exe"),"-encoding","UTF-8","-d",str(folder),str(policy),str(fixture)])
    checks=int(run("run",[str(java/"java.exe"),"-cp",str(folder),"org.majiro.player.LivePolicyChecks"]))
    boot=(ROOT/"player/runtime/player_boot.lua").read_text(encoding="utf8")
    update=boot[boot.index("local function update_online()"):boot.index("\nupdate_online()")]
    lua=create_lua();lua.globals()["UPDATE_SOURCE"]=update
    lua_checks=lua.execute("""
      local creates=0
      local selected='original'
      local HOST={text_mode=function()return selected end,translation=function()end,storage=function()end}
      local options={}
      local system={state={text_translation_ready=true}}
      local translation_tools={new=function(spec)
        assert(spec.mode=='online'and spec.game=='verified-source')
        creates=creates+1;return{stats={translated=0}}
      end}
      local module=function()return{}end
      local install=assert(loadstring('return function(HOST,options,system,translation_tools,module)\\n'..
        'local mode="original";local clock=0;local identity="verified-source";local opcodes,names={},{};local online\\n'..
        'local configuration={environment={NAMEPOPUP="1"}}\\n'..
        UPDATE_SOURCE..'\\nreturn update_online end'))()
      local update=install(HOST,options,system,translation_tools,module)
      update();assert(creates==0 and options.online_translation==nil)
      selected='online';update();assert(creates==1 and options.online_translation)
      update();assert(creates==1)
      selected='original';update();assert(options.online_translation==nil and system.state.text_translation_ready==nil)
      selected='online';update();assert(creates==2 and options.online_translation)
      return 5
    """)
    overlay=ROOT/"player/android/src/org/majiro/player/GameSettingsOverlay.java"
    text=overlay.read_text(encoding="utf8")
    assert"TranslationSettingsDialog.show(activity,worker,this::changed)"in text
    assert"activity.openOriginalMenu()"in text and"activity.requestExit()"in text
    assert"setOnTouchListener"in text and"performClick()"in text and"preferences.edit()"in text
    report={"status":"passed","timestamp":timestamp(),"java_mode_cases":checks,
        "lua_live_transition_cases":lua_checks,"device_accessed":False,"visual_verified":False,
        "evidence":str(folder.relative_to(ROOT)),"source_sha256":{
            str(path.relative_to(ROOT)):sha256(path)for path in(policy,overlay,ROOT/"player/runtime/player_boot.lua")}}
    write_json(ROOT/"reports/translation_live_controls_tests.json",report);write_json(folder/"report.json",report)
    print(json.dumps(report,ensure_ascii=True))
    return 0


if __name__=="__main__":raise SystemExit(main())
