"""Compile real Activity/key queue with deterministic Android boundary stubs."""
from __future__ import annotations

import argparse
import ast
from datetime import datetime
import hashlib
import json
from pathlib import Path
import shutil
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[2]

HARNESS = r"""package org.majiro.player;
import android.app.AlertDialog;
import android.os.*;
import android.view.KeyEvent;
import org.libsdl.app.SDLActivity;
public class TouchscreenBackTest {
 static int checks;
 static void check(boolean value,String why){checks++;if(!value)throw new AssertionError(why);}
 public static void main(String[] args)throws Exception{
  GameTouchKeys.reset();
  check(GameTouchKeys.consume()==0,"empty queue");
  GameTouchKeys.request(27);GameTouchKeys.request(27);GameTouchKeys.request(13);
  check(GameTouchKeys.consume()==3,"coalesced Escape and Enter");
  check(GameTouchKeys.consume()==0,"one shot key edges");
  for(int key:new int[]{37,38,39,40})GameTouchKeys.request(key);
  check(GameTouchKeys.consume()==60,"arrow key masks match native ABI");
  GameTouchKeys.request(33);GameTouchKeys.request(34);
  check(GameTouchKeys.consume()==192,"page keys append to the existing native ABI");
  check(GameTouchKeys.consume()==0,"page keys are one-shot edges");
  GameWindowCommands.reset();
  GameWindowCommands.request(3);GameWindowCommands.request(1);
  check(GameWindowCommands.consume()==1,"latest original menu command replaces the prior one");
  check(GameWindowCommands.consume()==0,"window command is consumed once");
  GameWindowCommands.request(99);GameWindowCommands.reset();
  check(GameWindowCommands.consume()==0,"window command cannot leak across games");
  boolean invalidMenu=false;
  try{GameWindowCommands.request(4);}catch(IllegalArgumentException expected){invalidMenu=true;}
  check(invalidMenu&&GameWindowCommands.consume()==0,"unknown window command rejected");
  GameTouchKeys.request(27);GameTouchKeys.reset();
  check(GameTouchKeys.consume()==0,"new game reset cannot leak an old key");
  boolean rejected=false;
  try{GameTouchKeys.request(4);}catch(IllegalArgumentException expected){rejected=true;}
  check(rejected&&GameTouchKeys.consume()==0,"Android back is never injected as PC key 4");
  Thread one=new Thread(()->{for(int i=0;i<5000;i++)GameTouchKeys.request(13);});
  Thread two=new Thread(()->{for(int i=0;i<5000;i++)GameTouchKeys.request(27);});
  one.start();two.start();one.join();two.join();
  check(GameTouchKeys.consume()==3,"UI producers preserve both pending key masks");
  GameActivity game=new GameActivity();game.onCreate(new Bundle());
  check(game.dispatcher.callback!=null,"predictive callback registered");
  game.onBackPressed();
  check(GameBridge.backs==1&&AlertDialog.shown==0&&game.finishes==0,"back delegates to original right click");
  GameSettingsOverlay.dismissNext=true;
  game.onBackPressed();
  check(GameBridge.backs==1,"drawer dismissal never also enters the game");
  game.dispatchKeyEvent(new KeyEvent(KeyEvent.ACTION_DOWN,false));
  game.dispatchKeyEvent(new KeyEvent(KeyEvent.ACTION_UP,true));
  check(GameBridge.backs==1,"down and cancelled key do not back twice");
  game.dispatchKeyEvent(new KeyEvent(KeyEvent.ACTION_UP,false));
  check(GameBridge.backs==2&&AlertDialog.shown==0,"committed hardware back");
  game.dispatcher.callback.onBackInvoked();
  check(GameBridge.backs==3&&game.finishes==0,"predictive back uses original input");
  game.sendGameKey(27);check(GameTouchKeys.consume()==2,"drawer Escape retains original key code");
  game.sendGameKey(13);check(GameTouchKeys.consume()==1,"drawer confirmation uses Enter");
  game.openWindowMenu(3);check(GameWindowCommands.consume()==3,"drawer save uses original window command");
  Looper.ui=false;game.requestGameBack();
  check(game.queue.size()==1&&GameBridge.backs==3,"off-thread back marshalled to UI");
  game.drain();check(GameBridge.backs==4,"UI back consumed once");
  game.requestExit();
  check(AlertDialog.shown==1&&GameBridge.cancelledMedia==0,"explicit library exit still asks first");
  game.requestGameBack();game.sendGameKey(27);game.openWindowMenu(3);
  check(GameBridge.backs==4&&GameTouchKeys.consume()==0,"exit confirmation cannot inject game input");
  check(GameWindowCommands.consume()==0,"exit confirmation cannot inject window menus");
  AlertDialog.latest.cancel();
  check(game.finishes==0&&GameBridge.cancelledMedia==0,"cancel preserves running game");
  game.onBackPressed();check(GameBridge.backs==5,"back works after cancel");
  java.util.concurrent.CountDownLatch stop=new java.util.concurrent.CountDownLatch(1);
  Thread nativeThread=new Thread(()->{try{stop.await();}catch(Exception e){throw new RuntimeException(e);}});
  nativeThread.start();SDLActivity.thread(nativeThread);
  game.requestExit();AlertDialog.latest.confirm();
  check(SDLActivity.quit==1&&game.finishes==0,"exit waits for native cleanup");
  check(GameBridge.cancelledMedia==1,"only confirmed exit cancels media");
  game.onBackPressed();game.sendGameKey(13);
  check(GameBridge.backs==5&&GameTouchKeys.consume()==0,"no input after exit");
  stop.countDown();nativeThread.join();SDLActivity.thread(null);
  game.finish();game.onDestroy();
  check(game.dispatcher.callback==null,"callback removed on destroy");
  game.onBackPressed();check(GameBridge.backs==5,"destroyed activity cannot send input");
  Build.VERSION.SDK_INT=26;
  GameActivity legacy=new GameActivity();legacy.onCreate(new Bundle());legacy.onBackPressed();
  check(GameBridge.backs==6&&legacy.finishes==0,"legacy back remains in game");
  check(legacy.superBack==0,"SDL immediate exit bypassed");
  System.out.println("Touchscreen back checks: "+checks);
 }
}"""


def read_stubs(path: Path) -> dict[str, str]:
    module = ast.parse(path.read_text(encoding="utf8"))
    for node in module.body:
        if isinstance(node, ast.Assign) and any(
                isinstance(target, ast.Name) and target.id == "STUBS" for target in node.targets):
            return ast.literal_eval(node.value)
    raise ValueError("Lifecycle boundary stubs unavailable")


def run(root: Path, evidence: Path) -> dict:
    stubs = read_stubs(root / "scripts/analysis/test_android_exit_lifecycle.py")
    stubs.pop("org/majiro/player/ExitLifecycleTest.java")
    stubs["org/majiro/player/GameBridge.java"] = """package org.majiro.player;public class GameBridge{
 public static int opens,pauses,resumes,closes,cancelledMedia,backs;
 public static void open(android.app.Activity a,String u,String r){opens++;GameTouchKeys.reset();}
 public static void pause(){pauses++;}public static void resume(){resumes++;}public static void close(){closes++;}
 public static void cancelPendingMedia(){cancelledMedia++;}
 public static void requestOriginalMenu(){backs++;}
 public static void requestGameKey(int key){GameTouchKeys.request(key);}
 public static void requestWindowMenu(int command){GameWindowCommands.request(command);}
}"""
    stubs["org/majiro/player/GameSettingsOverlay.java"] = """package org.majiro.player;
public class GameSettingsOverlay{
 public static boolean dismissNext;
 public GameSettingsOverlay(GameActivity activity){}
 public boolean dismiss(){boolean value=dismissNext;dismissNext=false;return value;}
 public void close(){}
}"""
    stubs["org/majiro/player/TouchscreenBackTest.java"] = HARNESS
    source, classes = evidence / "src", evidence / "classes"
    files = []
    for name, text in stubs.items():
        path = source / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf8")
        files.append(path)
    for name in ("GameActivity.java", "GameTouchKeys.java", "GameWindowCommands.java"):
        files.append(root / "player/android/src/org/majiro/player" / name)
    javac = shutil.which("javac")
    java = shutil.which("java")
    if not javac or not java:
        raise RuntimeError("JDK required for Activity/key tests")
    commands = {
        "javac": [javac, "-encoding", "UTF-8", "-source", "8", "-target", "8",
                  "-d", str(classes), *map(str, files)],
        "jvm": [java, "-cp", str(classes), "org.majiro.player.TouchscreenBackTest"],
    }
    results = {}
    for name, command in commands.items():
        completed = subprocess.run(command, capture_output=True, timeout=45)
        (evidence / (datetime.now().strftime("%Y%m%d") + "_" + name + ".txt")).write_bytes(
            completed.stdout + completed.stderr)
        if completed.returncode:
            raise RuntimeError(f"{name} failed; see {evidence}")
        results[name] = completed.stdout.decode("utf8", errors="replace").strip()
    overlay = (root / "player/android/src/org/majiro/player/GameSettingsOverlay.java").read_text(encoding="utf8")
    bridge = (root / "player/android/src/org/majiro/player/GameBridge.java").read_text(encoding="utf8")
    host = (root / "player/android/game_host.cpp").read_text(encoding="utf8")
    assert 'row(panel,"返回上一层（右键）",()->{dismiss();activity.openOriginalMenu();});' in overlay
    assert 'activity.sendGameKey(27);' in overlay
    assert 'activity.sendGameKey(13);' in overlay and 'activity.requestExit();' in overlay
    assert 'activity.sendGameKey(33);' in overlay and 'activity.sendGameKey(34);' in overlay
    assert "public static void requestGameKey(int key){GameTouchKeys.request(key);}" in bridge
    assert "GameTouchKeys.reset();" in bridge
    assert 'const int virtual_keys[]={13,27,37,38,39,40,33,34};' in host
    assert 'case SDLK_PAGEUP:code=33;break;case SDLK_PAGEDOWN:code=34;break;' in host
    injection = host[host.index('// Keep pending touchscreen actions queued'):host.index('if(cursor_warp_pending)')]
    assert injection.index("if(!paused)") < injection.index("consumeGameButtons")
    assert 'if(requested||keys||window_command)++input_activity;' in injection
    assert 'consumeWindowMenu' in injection
    production = {str(path.relative_to(root)): hashlib.sha256(path.read_bytes()).hexdigest()
                  for path in files[-3:]}
    return {
        "status": "passed", "timestamp": datetime.now().astimezone().isoformat(),
        "jvm_checks": int(results["jvm"].rsplit(" ", 1)[1]),
        "native_wiring_source_checks": True, "sources": production, "device_accessed": False,
        "scope": "Real Java Activity and atomic queue with Android stubs; native/menu wiring inspected.",
        "limitations": [
            "Native JNI and SDL execution needs the full Android build.",
            "Touchscreen interaction and exact game recollection return need device testing.",
        ],
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=ROOT)
    args = parser.parse_args()
    root = args.root.resolve()
    now = datetime.now()
    evidence = root / "archive" / now.strftime("%Y-%m") / (now.strftime("%Y%m%d") + "_touchscreen_back")
    evidence.mkdir(parents=True, exist_ok=True)
    report = run(root, evidence)
    target = root / "reports/touchscreen_back_semantics_tests.json"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf8")
    print(f"Touchscreen back: {report['status']}; {report['jvm_checks']} JVM checks")
    return 0


if __name__ == "__main__":
    sys.exit(main())
