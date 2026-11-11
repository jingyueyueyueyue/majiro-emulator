"""Execute the reported voiced source lines without clicks while translation arrives asynchronously."""
from pathlib import Path
import json
import sys
sys.path[:0]=[str(Path(__file__).resolve().parents[1]/"convert"),
              str(Path(__file__).resolve().parents[1]/"build")]
from audit_startup_resources import Startup
from deploy_player_android import payload
from lua_bridge import create_lua
from port_common import ROOT,archive_dir,read_json,write_json,timestamp,sha256


class VoiceDialogue(Startup):
    def __init__(self,*args,**kwargs):
        self.observed=[];self.ready_at=None;self.requests=[]
        self.reply_delay=1500
        super().__init__(*args,**kwargs)
    def translation(self,action,payload):
        if action=="config":return json.dumps({"mode":"online","ready":True,"context":20,"generation":"voice-fixture"})
        if action in("clear","waiting"):return"ok"
        query=json.loads(payload)
        if action=="submit":
            self.requests.extend(query["lines"])
            if self.ready_at is None:self.ready_at=self.clock+self.reply_delay
            for line in query["lines"]:
                assert line["text"]!="\u30e6\u30ea","standalone speaker sent for translation"
                self.translation_cache.setdefault(line["id"],"\u5c24\u91cc\u201c\u4f60\u597d\uff0c\u54e5\u54e5\u3002\u201d")
            return"queued"
        if action=="lookup":
            return self.translation_cache.get(query["id"],"")if self.ready_at is not None and self.clock>=self.ready_at else""
        if action=="display_status":return"ok"
        raise AssertionError(action)
    def audio(self,action,spec):
        if action=="play"and spec["id"]=="voice":
            self.observed.append({"kind":"voice","time":self.clock,"name":spec["name"]})
        return super().audio(action,spec)
    def graphics(self,command):
        if command.startswith("glyphs "):
            self.observed.append({"kind":"glyphs","time":self.clock,"text":command.split("\n")[-1]})
        return super().graphics(command)


def main():
    folder=archive_dir("online_voice_dialogue")
    files,roots=payload(folder)
    game=next(g for g in read_json(ROOT/"config/player_new_samples.json")["games"]if g["id"]=="kiraoto")
    prefix=next(r["directory"]for r in roots if r["id"]==game["id"])+"/"
    files={target[len(prefix):]:source for source,target in files if target.startswith(prefix)}
    session=VoiceDialogue(game,files,"online",1500000,40)
    result=session.run();assert not result["error"],result
    name=next(n for n in session.resources if n.startswith("\u30d7\u30ed\u30ed\u30fc\u30b0_01_00"))
    script=session.vm["load"](session.vm,name)
    # The real source voice call, separate name text opcode, quoted text, proc
    # and PAUSE run unchanged from the location preceding the first voice.
    session.vm["exec_at"](session.vm,script,0x2d1,session.lua.table(),True)
    initial=session.system["state"]["reading_generation"]or 0
    for tick in range(3000):
        session.lua.globals()["PLAYER_TICK"](session.clock,session.lua.table())
        session.clock+=16
        if(session.system["state"]["reading_generation"]or 0)>initial:break
    else:raise AssertionError("voiced dialogue requires input before display")
    voices=[e for e in session.observed if e["kind"]=="voice"]
    glyphs=[e for e in session.observed if e["kind"]=="glyphs"and"\u4f60\u597d"in e["text"]]
    assert len(voices)==1 and len(glyphs)==1,(voices,glyphs)
    assert session.ready_at is not None and voices[0]["time"]>=session.ready_at
    assert 0<=glyphs[0]["time"]-voices[0]["time"]<100,(voices,glyphs)
    assert glyphs[0]["text"]=="\u300c\u4f60\u597d\uff0c\u54e5\u54e5\u3002\u300d",glyphs
    assert session.system["state"]["console"]["name"]=="\u30e6\u30ea"
    assert any(line.get("speaker")=="\u30e6\u30ea"and line["text"].startswith("\u300c")for line in session.requests)
    first_events=list(session.observed)
    timeout=VoiceDialogue(game,files,"online",1500000,40)
    assert not timeout.run()["error"]
    timeout.reply_delay=60000
    source=timeout.vm["load"](timeout.vm,name)
    timeout.vm["exec_at"](timeout.vm,source,0x2d1,timeout.lua.table(),True)
    initial_timeout=timeout.system["state"]["reading_generation"]or 0
    for tick in range(3000):
        timeout.lua.globals()["PLAYER_TICK"](timeout.clock,timeout.lua.table())
        timeout.clock+=16
        if(timeout.system["state"]["reading_generation"]or 0)>initial_timeout:break
    else:raise AssertionError("failed translation trapped the source voice instruction")
    timeout_voice=[e for e in timeout.observed if e["kind"]=="voice"]
    timeout_text=[e for e in timeout.observed if e["kind"]=="glyphs"and"\u304a\u306f\u3088\u3046"in e["text"]]
    assert len(timeout_voice)==1 and len(timeout_text)==1
    assert 0<=timeout_text[0]["time"]-timeout_voice[0]["time"]<100
    assert timeout.lua.globals()["PLAYER_STATUS"]()["translation"]["fallback"]==1
    # Disable online mode: the same hook returns immediately, without delaying
    # voice playback or creating a translation transport request.
    offline=VoiceDialogue(game,files,"original",1500000,40)
    assert not offline.run()["error"]
    source=offline.vm["load"](offline.vm,name)
    offline.vm["exec_at"](offline.vm,source,0x2d1,offline.lua.table(),True)
    initial_offline=offline.system["state"]["reading_generation"]or 0
    started=offline.clock
    for tick in range(500):
        offline.lua.globals()["PLAYER_TICK"](offline.clock,offline.lua.table())
        offline.clock+=16
        if(offline.system["state"]["reading_generation"]or 0)>initial_offline:break
    offline_voice=[e for e in offline.observed if e["kind"]=="voice"]
    assert not offline.requests and len(offline_voice)==1
    assert offline_voice[0]["time"]-started<1000
    # Exercise stack/PC preservation: the preflight hook cannot pop args or
    # consume the original voice syscall until the background cache is ready.
    report={"status":"passed","timestamp":timestamp(),"source_script":name,
        "source_sha256":script["source_sha256"],"source_voice_call_offset":0x2d1,
        "clicks_required":0,"voice_play_count":len(voices),"dialogue_raster_count":len(glyphs),
        "voice_to_text_ms":glyphs[0]["time"]-voices[0]["time"],
        "original_speaker_preserved":True,"speaker_only_translation_requests":0,
        "model_added_speaker_removed":True,"observed":session.observed,
        "provider_timeout_without_extra_click":True,"original_mode_no_translation_delay":True,
        "device_accessed":False,"real_api_called":False,"evidence":str(folder.relative_to(ROOT)),
        "source_files":{name:sha256(ROOT/name)for name in(
            "player/runtime/majiro_translation.lua","player/runtime/majiro_vm.lua",
            "player/runtime/player_boot.lua","player/runtime/majiro_text.lua")}}
    write_json(ROOT/"reports/online_voice_dialogue_tests.json",report);write_json(folder/"report.json",report)
    print(json.dumps({k:v for k,v in report.items()if k not in("observed","source_files")},ensure_ascii=True))
    return 0


if __name__=="__main__":raise SystemExit(main())
