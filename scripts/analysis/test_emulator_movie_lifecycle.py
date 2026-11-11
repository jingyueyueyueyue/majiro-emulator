"""Execute MovieTextures.Session's production lifecycle method with queued UI work."""
from pathlib import Path
import json
import subprocess
import sys

from test_audio_preparation_cleanup import method
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "convert"))
from port_common import ROOT, read_json, sha256, timestamp, write_json


def run():
    folder = ROOT.parent/"archive/2026-11/20261111_emulator_audit_host/movie_lifecycle"
    folder.mkdir(parents=True, exist_ok=True)
    production = ROOT/"player/android/src/org/majiro/player/MovieTextures.java"
    pause = method(production.read_text(encoding="utf8"), "void pause(final boolean value)")
    source = r"""
import java.util.*;
public class MovieLifecycleChecks{
 boolean closed,finished,prepared,seeking,suspended;String error;
 Player player=new Player();QueueHandler handler=new QueueHandler();
 static class Player{
  int starts,pauses;boolean ready,playing,fail;
  boolean isPlaying(){return playing;}
  void start(){if(!ready||fail)throw new IllegalStateException("unprepared");starts++;playing=true;}
  void pause(){pauses++;playing=false;}
 }
 static class QueueHandler{
  ArrayList<Runnable> queue=new ArrayList<>();
  void post(Runnable r){queue.add(r);}
  void drain(){for(Runnable r:queue)r.run();queue.clear();}
 }
 void fail(String value){error=value;finished=true;}
 // METHOD
 static void check(boolean condition,String message){if(!condition)throw new AssertionError(message);}
 public static void main(String[]args){
  MovieLifecycleChecks s=new MovieLifecycleChecks();
  s.pause(false);s.handler.drain();
  check(s.player.starts==0&&s.error==null,"resume started preparing decoder");
  s.prepared=s.player.ready=true;
  s.pause(false);s.pause(true);s.handler.drain();
  check(s.player.starts==0,"stale resume escaped later suspension");
  s.pause(false);s.handler.drain();check(s.player.starts==1,"prepared resume missing");
  s.pause(true);s.handler.drain();check(s.player.pauses==1,"pause missing");
  s.seeking=true;s.pause(false);s.handler.drain();check(s.player.starts==1,"resume escaped seek");
  s.seeking=false;s.player.fail=true;s.pause(false);s.handler.drain();
  check(s.finished&&s.error.startsWith("movie lifecycle:"),"lifecycle exception escaped GL thread");
  s.player.fail=false;s.pause(false);s.handler.drain();
  check(s.player.starts==1,"completed decoder restarted");
  s.finished=false;s.closed=true;s.pause(false);s.handler.drain();
  check(s.player.starts==1,"closed decoder restarted");
  System.out.println("{\"status\":\"passed\",\"checks\":8}");
 }
}
"""
    target = folder/"MovieLifecycleChecks.java"
    target.write_text(source.replace("// METHOD", pause), encoding="utf8")
    java = Path(read_json(ROOT/"reports/android_toolchain.json")["java"])/"bin"
    for index, command in enumerate((
        [str(java/"javac.exe"), "-encoding", "UTF-8", "-d", str(folder), str(target)],
        [str(java/"java.exe"), "-cp", str(folder), "MovieLifecycleChecks"],
    )):
        result = subprocess.run(command, capture_output=True, timeout=30)
        (folder/f"20261111_{index}.txt").write_bytes(result.stdout+result.stderr)
        assert result.returncode == 0, result.stderr.decode("utf8", errors="replace")
    report = json.loads(result.stdout)
    report.update(timestamp=timestamp(), source_sha256=sha256(production),
                  device_accessed=False, scope="production Session.pause method with queued handler and decoder stub")
    write_json(folder/"20261111_result.json", report)
    print(json.dumps(report))


if __name__ == "__main__":
    run()
