"""Execute production Android fade/callback code against controllable decoders."""
from pathlib import Path
import json
import subprocess
import sys

from test_audio_preparation_cleanup import method, MOCK
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "convert"))
from port_common import ROOT, read_json, sha256, timestamp, write_json

EVIDENCE = ROOT.parent / "archive/2026-11/20261111_emulator_audit_host/audio_fades"
HARNESS = r"""
 static void check(boolean value,String why){if(!value)throw new AssertionError(why);}
 static void waitFade()throws Exception{
  long until=System.nanoTime()+2000000000L;
  while(System.nanoTime()<until){
   synchronized(GameBridge.class){if(audioFades.isEmpty())return;}
   Thread.sleep(5);
  }
  throw new AssertionError("fade timeout");
 }
 public static void main(String[]args)throws Exception{
  audio("play","music","intro.ogg","loop.ogg",true,1000,0,0,0,false);
  MediaPlayer first=players.get("music"),next=nextPlayers.get("music");
  audio("fadeout","music","","",false,1000,0,120,0,false);
  Thread.sleep(20);
  synchronized(GameBridge.class){
   first.playing=false;next.playing=true;first.completion.onCompletion(first);
  }
  waitFade();
  check(first.dead&&next.dead&&!players.containsKey("music"),"intro-loop promotion lost fadeout");
  audio("play","music","one.ogg","",true,1000,0,0,0,false);
  MediaPlayer owner=players.get("music");
  audio("volume","music","","",false,0,0,180,0,false);
  Thread.sleep(20);
  audio("volume","music","","",false,800,0,30,0,false);
  waitFade();Thread.sleep(190);
  check(Math.abs(owner.left-.8f)<.001f,"older fade overwrote newer gain");
  audio("volume","music","","",false,0,0,150,0,false);
  Thread.sleep(20);
  audio("volume","music","","",false,400,0,0,0,false);
  Thread.sleep(180);
  check(Math.abs(owner.left-.4f)<.001f,"old fade overwrote immediate gain");
  audio("fadeout","music","","",false,400,0,80,0,false);
  audio("play","music","replacement.ogg","",true,700,0,0,0,false);
  MediaPlayer replacement=players.get("music");
  owner.completion.onCompletion(owner);
  owner.error.onError(owner,1,2);
  Thread.sleep(110);
  check(!replacement.dead&&players.get("music")==replacement,"stale fade destroyed replacement");
  check(!audioCompletion.get("music")&&mediaError==null,"stale completion/error contaminated replacement");
  replacement.completion.onCompletion(replacement);
  check(audioCompletion.get("music"),"live completion latch missing");
  audio("stat_reset","music","","",false,0,0,0,0,false);
  check(!audioCompletion.get("music"),"stat reset did not clear latch");
  audio("fadeout","music","","",false,700,0,0,0,false);
  check(replacement.dead&&!players.containsKey("music"),"zero fade not synchronous");
  check(audioSourcePaths.isEmpty()&&levels.isEmpty()&&audioFades.isEmpty(),"stop leaked transport metadata");
  System.out.println("{\"status\":\"passed\",\"checks\":9}");
 }
}
"""


def run():
    source = ROOT/"player/android/src/org/majiro/player/GameBridge.java"
    text = source.read_text(encoding="utf8")
    parts = [method(text, signature) for signature in (
        "public static synchronized boolean audio(", "private static void applyVolume(",
        "private static void startAudioFade(", "private static void observeAudio(",
        "private static void promoteAudio(", "private static MediaPlayer currentAudio(")]
    stub = MOCK[:MOCK.index(" public static void main(")] + HARNESS
    stub = stub.replace("boolean dead,playing;", "boolean dead,playing;float left,right;\n"
                        "  OnCompletionListener completion;OnErrorListener error;")
    stub = stub.replace('check("volume");}', 'check("volume");left=l;right=r;}')
    stub = stub.replace("setOnCompletionListener(OnCompletionListener l){}",
                        "setOnCompletionListener(OnCompletionListener l){completion=l;}")
    stub = stub.replace("setOnErrorListener(OnErrorListener l){}",
                        "setOnErrorListener(OnErrorListener l){error=l;}")
    EVIDENCE.mkdir(parents=True, exist_ok=True)
    src = EVIDENCE/"20261111_GameBridge.java"
    # javac requires the public class's filename.
    src = EVIDENCE/"GameBridge.java"
    src.write_text(stub.replace("// METHODS", "\n".join(parts)), encoding="utf8")
    clock = EVIDENCE/"SystemClock.java"
    clock.write_text("""package android.os;public final class SystemClock{
      public static long uptimeMillis(){return System.nanoTime()/1000000;}
      public static void sleep(long n){try{Thread.sleep(n);}catch(InterruptedException e){throw new RuntimeException(e);}}}""",
                     encoding="utf8")
    java = Path(read_json(ROOT/"reports/android_toolchain.json")["java"])/"bin"
    commands = [
        [str(java/"javac.exe"), "-encoding", "UTF-8", "-d", str(EVIDENCE), str(src), str(clock),
         str(ROOT/"player/android/src/org/majiro/player/AudioLifecycle.java")],
        [str(java/"java.exe"), "-cp", str(EVIDENCE), "org.majiro.player.GameBridge"],
    ]
    for index, command in enumerate(commands):
        result = subprocess.run(command, capture_output=True, timeout=30)
        (EVIDENCE/f"20261111_{index}.txt").write_bytes(result.stdout+result.stderr)
        assert result.returncode == 0, result.stderr.decode("utf8", errors="replace")
    report = json.loads(result.stdout)
    report.update(timestamp=timestamp(), source_sha256=sha256(source), device_accessed=False,
                  scope="unchanged production Java audio/fade/completion methods; controlled decoder stub")
    write_json(EVIDENCE/"20261111_result.json", report)
    print(json.dumps(report))


if __name__ == "__main__":
    run()
