"""Cross-build original voice stop, suppression, resource and gain behavior."""
from __future__ import annotations

from collections import Counter
import hashlib
import itertools
import json
from pathlib import Path
import sys

from audit_player_semantics import registered_providers,source_identity
from original_voice_playback import VoicePlaybackOracle

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/"convert"))
from port_common import ROOT,archive_dir,timestamp,write_json


RESET="""
function(mod,ops,system,filename,force,mode,male,skip,master,gain,male_enabled,female_enabled,fast,previous)
  local script={name='voice-playback-fixture',main=0,functions={},code={{0,0x191}}}
  local vm=mod.new{opcodes=ops,loader=function()return script end,syscall=system.invoke}
  vm:begin(script.name)
  local state,media=system.state,system.state.media
  state.settings.voicevolume=master
  state.settings.talk_male=male_enabled;state.settings.talk_female=female_enabled
  state.settings.fast_mode=fast
  media.channels.voice=previous and{id='voice',name='previous',file='previous.wav',
    volume=1000,channel_volume=1000,started=0,duration_ms=1000,generation=0}or nil
  media.voice_volume=gain;media.voice_previous_volume=321
  media.voice_requested='prior'
  media.voice_tag(vm,filename,force,mode,male,skip)
  return media.voice_volume,media.voice_previous_volume,media.channels.voice~=nil
end
"""


def cases():
    for force,mode,male,skip,fast,master,gain in itertools.product(
            (0,1),(0,1),(0,1),(0,1),(False,True),(0,700,1000),(400,1000)):
        yield{"filename":"voice","force":force,"mode":mode,"male":male,"skip":skip,
            "fast":fast,"master":master,"gain":gain}
    for male_enabled,female_enabled,male,mode in itertools.product((0,1,2),(0,1,2),(0,1),(0,1)):
        yield{"filename":"voice","male_enabled":male_enabled,"female_enabled":female_enabled,
              "male":male,"mode":mode}
    for filename,files,previous in itertools.product(
            ("missing","voice.ogg","sub\\Voice","##nw##","##other","","\u3042"),
            (("voice.wav","voice.ogg"),("##nw##.wav","##other.wav"),(".wav","\u3042.ogg")),
            (False,True)):
        yield{"filename":filename,"files":files,"previous":previous,"gain":413}
    for master,gain in itertools.product((1,700,1000,-1,2147483647),
                                        (0,-1,1,413,1001,2147483647)):
        yield{"filename":"voice","master":master,"gain":gain}


def run():
    samples=json.loads((ROOT/"reports/opcode_dispatch.json").read_text(encoding="utf-8"))["samples"]
    dispatch=json.loads((ROOT/"reports/syscall_dispatch.json").read_text(encoding="utf-8"))["samples"]
    by_name={row["sample"]:row for row in dispatch}
    before=source_identity()
    report={"schema":1,"timestamp":timestamp(),"source_identity":before,"samples":[],"failures":[],
        "scope":"actual native voice routine, old-stream stop, WAV/OGG resolution, force/fast and gender gating, skip, no-voice markers, gain consumption and signed arithmetic",
        "host_boundaries":["single-thread critical sections","fixture VFS membership",
                           "host MSVCRT CP932 path splitting","sampled fast-input state","final decoder entry"],
        "limits":["voice continuation and per-play panning are held at zero",
                  "hardware decoding, ducking and lifecycle remain separate validation targets",
                  "this is the voice routine boundary, not a claim about every raw-API wrapper"],
        "complete_engine_semantics":False}
    for sample in samples:
        if not any(row["name"]=="$music_pause" for row in by_name[sample["sample"]]["entries"]):
            from original_legacy_voice import LegacyVoiceProfileOracle
            from test_emulator_legacy_audio import voice_checks
            legacy=LegacyVoiceProfileOracle(sample,by_name[sample["sample"]]["entries"])
            checks=voice_checks(legacy)
            report["samples"].append({"sample":sample["sample"],"exe_sha256":sample["exe_sha256"],
                "comparisons":len(checks),"mismatches":0,"voice_profile":legacy.profile(),
                "scope":"original legacy voice marker/forced-skip behavior",
                "cases":checks})
            continue
        oracle=VoicePlaybackOracle(sample,by_name[sample["sample"]]["entries"])
        profile=oracle.profile()
        events=[];reads=[];lua_ref={}
        def asset(name):
            reads.append(name)
            return lua_ref["lua"].table_from({"file":name,"duration_ms":1000})
        def audio(action,spec):
            events.append({"action":action,"filename":spec["file"],"gain":spec["volume"]})
            return True
        counts,failures=Counter(),Counter()
        current_files=None
        for fixture in cases():
            config={"filename":"voice","force":0,"mode":1,"male":0,"skip":0,
                "files":("voice.wav",),"master":1000,"gain":1000,"male_enabled":1,
                "female_enabled":1,"fast":False,"previous":True}
            config.update(fixture);events.clear();reads.clear();counts["voice_routine"]+=1
            if current_files!=config["files"]:
                lua,_,_,system=registered_providers(overrides={"voice_controls":profile,
                    "resource_names":{name:True for name in config["files"]},"media_asset":asset,"audio":audio})
                lua_ref["lua"]=lua
                module=lua.execute((ROOT/"player/runtime/majiro_vm.lua").read_text(encoding="utf-8"))
                ops=lua.execute((ROOT/"player/tables/opcodes.lua").read_text(encoding="utf-8"))
                execute=lua.eval(RESET);current_files=config["files"]
            reason=None
            try:
                expected=oracle.run_voice(**config)
                gain,previous_gain,current=execute(module,ops,system,*[
                    config[key]for key in("filename","force","mode","male","skip","master","gain",
                                        "male_enabled","female_enabled","fast","previous")])
                plays=[{"filename":row["filename"],"gain":row["gain"]}
                       for row in events if row["action"]=="play"]
                native_plays=[{"filename":row["filename"].lower(),"gain":row["gain"]}
                              for row in expected["plays"]]
                if plays!=native_plays:reason=f"plays {plays} != {native_plays}"
                elif gain!=expected["gain"]:reason=f"next gain {gain} != {expected['gain']}"
                elif expected["previous_gain"]is not None and previous_gain!=expected["previous_gain"]:
                    reason=f"previous gain {previous_gain} != {expected['previous_gain']}"
                elif bool([row for row in events if row["action"]=="stop"])!=(
                        config["previous"]and not expected["old_active"]):
                    reason="old voice stop differs"
                elif len(reads)!=len(expected["plays"]):reason="suppressed or absent voice requested decoding"
                elif current!=bool(expected["plays"]or expected["old_active"]):reason="voice ownership differs"
            except Exception as error:reason=repr(error)
            if reason:
                failures["voice_routine"]+=1
                if len(report["failures"])<80:report["failures"].append({
                    "sample":sample["sample"],"fixture":config,"reason":reason})
        report["samples"].append({"sample":sample["sample"],"exe_sha256":sample["exe_sha256"],
            "comparisons":sum(counts.values()),"mismatches":sum(failures.values()),"voice_profile":profile,
            "voice_entry":oracle.voice_va,"decoder_entry":oracle.voice_decoder_va,
            "voice_handle_slot":oracle.voice_slot,"gain_slot":oracle.gain_slot})
        print(f"{sample['sample']}: {sum(counts.values())} voice playbacks; "
              f"{sum(failures.values())} mismatches",flush=True)
    report["comparisons"]=sum(row["comparisons"]for row in report["samples"])
    report["mismatches"]=sum(row["mismatches"]for row in report["samples"])
    report["source_changed_during_test"]=source_identity()!=before
    report["test_sources"]={name:hashlib.sha256((ROOT/"scripts/analysis"/name).read_bytes()).hexdigest()
        for name in("original_opcode.py","original_strings.py","original_calls.py","native_function.py",
                    "original_audio.py","original_extsound.py","original_text.py",
                    "original_voice_settings.py","original_voice_control.py","original_voice_playback.py",
                    "test_voice_playback.py")}
    report["status"]="passed"if not report["mismatches"]and not report["source_changed_during_test"]else"failed"
    folder=archive_dir("voice_playback")
    write_json(folder/(folder.name[:8]+"_voice_playback.json"),report)
    write_json(ROOT/"reports/voice_playback_tests.json",report)
    return 0 if report["status"]=="passed"else 1


if __name__=="__main__":raise SystemExit(run())
