"""Intro/loop handoff and stale finish callbacks at the Artemis tag boundary."""
from lua_bridge import create_lua, table_value
from port_common import ROOT, write_json


def run():
    lua = create_lua()
    lua.globals()["AUDIO"] = lua.execute((ROOT / "runtime/artemis_audio.lua").read_text(encoding="utf-8"))
    cases = table_value(lua.execute("""
      local e={tags={},clock=0,tag=function(self,t)self.tags[#self.tags+1]=t end,
        now=function(self)return self.clock end}
      local a=AUDIO.install(e)
      local cases={}
      local function latest(kind)
        for i=#e.tags,1,-1 do if e.tags[i][1]==kind then return e.tags[i]end end
      end
      local s={id='music',generation=1,file='intro.wav',loop_file='repeat.ogg',
        duration_ms=1200,loop_duration_ms=900,volume=700,pan=0,loop=true}
      a.command('play',s)
      local first=latest('setonsoundfinish')
      assert(latest('splay').file=='intro.wav'and latest('splay').loop==0)
      assert(a.state.tracks.ruri_audio_music.phase=='intro')
      s.volume=350;s.fade_ms=0;a.command('volume',s)
      ruri_audio_finish(e,first)
      assert(latest('splay').file=='repeat.ogg'and latest('splay').loop==1)
      assert(latest('splay').gain==350)
      assert(a.state.tracks.ruri_audio_music.duration==900)
      assert(a.state.tracks.ruri_audio_music.phase=='loop')
      cases[#cases+1]='intro completes into looping second file with current gain'
      local count=#e.tags
      ruri_audio_finish(e,first)
      assert(#e.tags==count and not a.state.tracks.ruri_audio_music.finished)
      cases[#cases+1]='duplicate intro callback does not end the loop'
      local second=latest('setonsoundfinish')
      s.generation=2;s.file='replacement.ogg';s.loop_file=nil
      a.command('play',s);count=#e.tags
      ruri_audio_finish(e,second)
      assert(#e.tags==count and not a.state.tracks.ruri_audio_music.finished)
      cases[#cases+1]='replaced song ignores previous finish callback'
      local replacement=latest('setonsoundfinish')
      a.command('stop',s);count=#e.tags
      ruri_audio_finish(e,replacement)
      assert(#e.tags==count and a.state.tracks.ruri_audio_music==nil)
      cases[#cases+1]='stopped song cannot be resurrected by a delayed callback'
      s.generation=3;s.loop=false;a.command('play',s)
      ruri_audio_finish(e,latest('setonsoundfinish'))
      assert(a.state.tracks.ruri_audio_music.finished)
      cases[#cases+1]='one-shot finish remains observable'
      return cases
    """))
    report = {"status": "passed", "cases": cases,
              "scope": "independent transport callback ordering; real decoder handoff checked separately"}
    write_json(ROOT / "reports/audio_transport_tests.json", report)
    print("audio transport:", len(cases), "passed")
    return report


if __name__ == "__main__":
    run()
