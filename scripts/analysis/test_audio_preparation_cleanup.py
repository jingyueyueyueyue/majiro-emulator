"""Run the unchanged production audio method with decoder failures at ownership boundaries."""
from pathlib import Path
import json
import subprocess
import sys
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/"convert"))
from port_common import ROOT,archive_dir,read_json,sha256,timestamp,write_json


def method(source,signature):
    start=source.index(signature);at=source.index("{",start);depth=1;cursor=at+1
    while depth:
        if source[cursor]=='{':depth+=1
        elif source[cursor]=='}':depth-=1
        cursor+=1
    return source[start:cursor]


MOCK=r"""
package org.majiro.player;
import java.util.*;
public final class GameBridge {
 static String mediaError;
 static final HashMap<String,MediaPlayer> players=new HashMap<>(),nextPlayers=new HashMap<>();
 static final HashMap<String,Boolean> audioCompletion=new HashMap<>();
 static final HashMap<String,String> audioSourcePaths=new HashMap<>();
 static final HashMap<String,Integer> levels=new HashMap<>();
 static final AudioLifecycle<MediaPlayer> audioLifecycle=new AudioLifecycle<>(players,new AudioLifecycle.Transport<MediaPlayer>(){
  public boolean playing(MediaPlayer p){return p.isPlaying();}
  public boolean pause(MediaPlayer p){p.playing=false;return true;}
  public void start(MediaPlayer p){p.start();}
 });
 static final class MediaPlayer{
  static int created,released,failNth;static String phase="";
  final int id=++created;boolean dead,playing;
  interface OnCompletionListener{void onCompletion(MediaPlayer p);}
  interface OnErrorListener{boolean onError(MediaPlayer p,int what,int extra);}
  interface OnSeekCompleteListener{void onSeekComplete(MediaPlayer p);}
  void check(String at){if(id==failNth&&phase.equals(at))throw new IllegalStateException(at);}
  void setDataSource(String p){check("source");}
  void setLooping(boolean b){}
  void prepare(){check("prepare");}
  void setNextMediaPlayer(MediaPlayer p){check("next");}
  void setVolume(float l,float r){check("volume");}
  void setOnCompletionListener(OnCompletionListener l){}
  void setOnErrorListener(OnErrorListener l){}
  void setOnSeekCompleteListener(OnSeekCompleteListener l){}
  void start(){check("start");playing=true;}
  void pause(){playing=false;}
  boolean isPlaying(){return playing;}
  void seekTo(int offset){check("seek");}
  void release(){if(dead)throw new AssertionError("double release");dead=true;++released;}
 }
 // METHODS
 public static void main(String[]args)throws Exception{
  int checks=0;
  for(int nth:new int[]{1,2})for(String phase:new String[]{"source","prepare","volume","next","start","seek"}){
   if(nth==2&&(phase.equals("next")||phase.equals("start")||phase.equals("seek")))continue;
   MediaPlayer.created=MediaPlayer.released=0;MediaPlayer.failNth=nth;MediaPlayer.phase=phase;
   boolean failed=false;
   try{audio("play","music","first.ogg","second.ogg",true,700,0,0,phase.equals("seek")?1:0,false);}
   catch(IllegalStateException expected){failed=true;}
   if(!failed)throw new AssertionError("expected decoder boundary failure "+nth+" "+phase);
   if(!players.isEmpty()||!nextPlayers.isEmpty()||!audioSourcePaths.isEmpty()||!levels.isEmpty())
    throw new AssertionError("failed decoder remains published");
   if(MediaPlayer.created!=MediaPlayer.released)throw new AssertionError("unowned decoder leaked");
   audioLifecycle.reset();++checks;
  }
  MediaPlayer.created=MediaPlayer.released=0;MediaPlayer.failNth=0;MediaPlayer.phase="";
  audio("play","music","first.ogg","second.ogg",true,700,0,0,0,false);
  if(players.size()!=1||nextPlayers.size()!=1)throw new AssertionError("success lost ownership");
  audio("stop","music","","",false,0,0,0,0,false);
  if(MediaPlayer.created!=2||MediaPlayer.released!=2)throw new AssertionError("normal stop leaked");
  System.out.println("{\"status\":\"passed\",\"failure_boundaries\":"+checks+",\"normal_cleanup\":true}");
 }
}
"""


def main():
    source=ROOT/"player/android/src/org/majiro/player/GameBridge.java";text=source.read_text(encoding="utf8")
    parts=[method(text,signature)for signature in(
        "public static synchronized boolean audio(","private static void applyVolume(",
        "private static void observeAudio(","private static void promoteAudio(","private static MediaPlayer currentAudio(")]
    folder=archive_dir("audio_preparation_cleanup");src=folder/"GameBridge.java"
    src.write_text(MOCK.replace("// METHODS","\n".join(parts)),encoding="utf8")
    clock=folder/"SystemClock.java";clock.write_text("package android.os;public final class SystemClock{public static long uptimeMillis(){return System.nanoTime()/1000000;}public static void sleep(long n){}}",encoding="utf8")
    java=Path(read_json(ROOT/"reports/android_toolchain.json")["java"])/"bin"
    compile=subprocess.run([str(java/"javac.exe"),"-encoding","UTF-8","-d",str(folder),
        str(src),str(clock),str(ROOT/"player/android/src/org/majiro/player/AudioLifecycle.java")],
        capture_output=True,timeout=30)
    (folder/"compile.txt").write_bytes(compile.stdout+compile.stderr)
    if compile.returncode:raise RuntimeError("audio fixture compile: "+str(folder/"compile.txt"))
    result=subprocess.run([str(java/"java.exe"),"-cp",str(folder),"org.majiro.player.GameBridge"],
        capture_output=True,timeout=10)
    (folder/"run.txt").write_bytes(result.stdout+result.stderr)
    if result.returncode:raise RuntimeError("audio preparation cleanup failed: "+str(folder/"run.txt"))
    report=json.loads(result.stdout.decode("utf8"));report.update(timestamp=timestamp(),source_sha256=sha256(source),
        evidence=str(folder.relative_to(ROOT)),device_accessed=False,
        scope="unchanged production audio/volume/completion helpers and lifecycle, mocked Android MediaPlayer decoder boundaries")
    write_json(ROOT/"reports/audio_preparation_cleanup.json",report)
    print(json.dumps(report,ensure_ascii=False))
    return 0


if __name__=="__main__":raise SystemExit(main())
