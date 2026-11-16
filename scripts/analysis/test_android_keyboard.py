"""Exercise the real Android hardware-key dispatcher without devices or APKs."""
from __future__ import annotations

import ast
import json
from pathlib import Path
import re
import subprocess
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "convert"))
from port_common import ROOT, archive_dir, read_json, sha256, timestamp, write_json


HARNESS = r"""package org.majiro.player;
import android.app.AlertDialog;
import android.os.Bundle;
import android.view.KeyEvent;
import org.libsdl.app.SDLActivity;
import java.util.*;
public class HardwareKeyboardTest {
 static int checks;
 static void check(boolean value,String why){checks++;if(!value)throw new AssertionError(why);}
 static KeyEvent event(int action,int key,int device,int source,int flags,int repeat,boolean cancelled){
  return new KeyEvent(action,key,device,source,flags,repeat,cancelled);
 }
 static void down(GameActivity game,int key,int device){
  check(game.dispatchKeyEvent(event(0,key,device,257,0,0,false)),"keyboard down consumed");
 }
 static void up(GameActivity game,int key,int device){
  check(game.dispatchKeyEvent(event(1,key,device,257,0,0,false)),"keyboard up consumed");
 }
 static void expect(String... events){
  check(SDLActivity.keyEvents.equals(Arrays.asList(events)),"SDL edges "+SDLActivity.keyEvents+" expected "+Arrays.toString(events));
  SDLActivity.keyEvents.clear();
 }
 static void expectReleased(int... keys){
  List<String> wanted=new ArrayList<>();
  for(int key:keys)wanted.add("up:"+key);
  Collections.sort(wanted);
  List<String> actual=new ArrayList<>(SDLActivity.keyEvents);Collections.sort(actual);
  check(actual.equals(wanted),"each held key released once: "+SDLActivity.keyEvents);
  SDLActivity.keyEvents.clear();
 }
 public static void main(String[] args)throws Exception{
  GameActivity game=new GameActivity();game.onCreate(new Bundle());game.onResume();game.onWindowFocusChanged(true);
  check(game.inputManager.listener!=null&&game.inputManager.registered==1,"input-device listener registered");
  int[] keys={66,111,62,67,61,21,19,22,20,92,93,113,114,59,60,57,58,115};
  for(int key:keys){down(game,key,1);up(game,key,1);expect("down:"+key,"up:"+key);}
  for(int key=29;key<=54;key++){down(game,key,1);up(game,key,1);expect("down:"+key,"up:"+key);}
  for(int key=7;key<=16;key++){down(game,key,1);up(game,key,1);expect("down:"+key,"up:"+key);}
  check(game.superKeys==0,"game keys bypass Android focus navigation");
  down(game,160,1);up(game,160,1);expect("down:66","up:66");
  down(game,66,1);down(game,160,1);up(game,66,1);expect("down:66");
  up(game,160,1);expect("up:66");

  down(game,113,1);down(game,113,1);
  game.dispatchKeyEvent(event(0,113,1,257,0,4,false));expect("down:113");
  game.dispatchKeyEvent(event(1,113,1,257,0,0,true));expect("up:113");
  up(game,113,1);expect();
  game.dispatchKeyEvent(event(0,113,1,257,0,2,false));expect();
  game.dispatchKeyEvent(event(0,59,1,257,0,0,true));expect();
  game.dispatchKeyEvent(event(2,59,1,257,0,0,false));expect();
  down(game,62,-1);
  game.dispatchKeyEvent(event(1,62,-1,0,0,0,true));expect("down:62","up:62");

  down(game,113,1);down(game,113,2);up(game,113,1);expect("down:113");
  up(game,113,2);expect("up:113");
  down(game,113,1);down(game,114,1);up(game,113,1);expect("down:113","down:114","up:113");
  up(game,114,1);expect("up:114");
  down(game,113,1);down(game,113,2);expect("down:113");
  game.inputManager.listener.onInputDeviceRemoved(1);expect();
  game.inputManager.listener.onInputDeviceRemoved(2);expect("up:113");
  up(game,113,2);expect();
  down(game,59,1);down(game,62,1);expect("down:59","down:62");
  game.inputManager.listener.onInputDeviceChanged(1);expectReleased(59,62);
  game.inputManager.listener.onInputDeviceAdded(1);expect();

  down(game,113,1);down(game,59,1);down(game,62,1);expect("down:113","down:59","down:62");
  game.onWindowFocusChanged(false);expectReleased(113,59,62);
  down(game,66,1);expect();
  game.onWindowFocusChanged(true);
  game.dispatchKeyEvent(event(0,113,1,257,0,3,false));expect();
  up(game,113,1);expect();
  down(game,113,1);expect("down:113");
  game.onPause();expect("up:113");
  down(game,59,1);expect();
  game.onResume();up(game,113,1);expect();
  down(game,59,1);expect("down:59");
  game.releaseGameKeys();expect("up:59");
  GameSettingsOverlay.latest.showing=true;down(game,62,1);expect();
  GameSettingsOverlay.latest.showing=false;up(game,62,1);expect();
  down(game,62,1);expect("down:62");
  game.requestExit();expect("up:62");
  down(game,66,1);up(game,66,1);expect();
  AlertDialog.latest.cancel();
  down(game,66,1);up(game,66,1);expect("down:66","up:66");

  check(!game.dispatchKeyEvent(event(0,24,1,257,0,0,false)),"volume remains Android-owned");
  check(!game.dispatchKeyEvent(event(0,131,1,257,0,0,false)),"unsupported function keys retain SDL fallback");
  check(!game.dispatchKeyEvent(event(0,66,1,1025,0,0,false)),"gamepad is not stolen as hardware keyboard");
  check(!game.dispatchKeyEvent(event(0,66,-1,257,2,0,false)),"IME retains normal dispatch");
  check(!game.dispatchKeyEvent(event(0,66,-1,0,0,0,false)),"unknown source retains normal dispatch");
  expect();
  game.dispatchKeyEvent(event(0,66,1,0,0,0,false));up(game,66,1);expect("down:66","up:66");
  game.dispatchKeyEvent(event(0,66,1,257|8194,0,0,false));up(game,66,1);expect("down:66","up:66");
  java.lang.reflect.Field broken=SDLActivity.class.getDeclaredField("mBrokenLibraries");broken.setAccessible(true);
  broken.setBoolean(null,true);down(game,66,1);up(game,66,1);expect();broken.setBoolean(null,false);
  java.lang.reflect.Field surface=SDLActivity.class.getDeclaredField("mSurface");surface.setAccessible(true);
  Object oldSurface=surface.get(null);surface.set(null,null);down(game,66,1);expect();surface.set(null,oldSurface);

  int backs=GameBridge.menus;
  game.dispatchKeyEvent(event(0,4,1,257,0,0,false));game.dispatchKeyEvent(event(1,4,1,257,0,0,false));
  check(GameBridge.menus==backs+1,"Android back keeps original right-click behavior");expect();
  int[] pc={13,27,37,38,39,40,33,34};
  for(int i=0;i<pc.length;i++){
   GameTouchKeys.reset();GameTouchKeys.request(pc[i]);
   check(GameTouchKeys.consume()==1<<i,"existing touchscreen key ABI "+pc[i]);
   check(GameTouchKeys.consume()==0,"touchscreen key edge consumed once");
  }
  GameTouchKeys.request(13);GameTouchKeys.request(27);GameTouchKeys.reset();
  check(GameTouchKeys.consume()==0,"existing touchscreen reset clears pending edges");
  check(GameBridge.keys==0&&GameBridge.commands==0,"physical keys bypass one-shot queues and menus");
  down(game,113,1);down(game,59,1);down(game,62,1);expect("down:113","down:59","down:62");
  game.onDestroy();expectReleased(113,59,62);
  check(game.inputManager.listener==null&&game.inputManager.unregistered==1,"device listener released before teardown");
  down(game,66,1);up(game,66,1);expect();
  GameActivity second=new GameActivity();second.onCreate(new Bundle());second.onResume();
  down(second,62,1);second.requestExit();expect("down:62","up:62");AlertDialog.latest.confirm();
  down(second,62,1);up(second,62,1);expect();second.onDestroy();expect();
  System.out.println("Hardware keyboard checks: "+checks);
 }
}"""


def stubs_from(path: Path) -> dict[str, str]:
    for node in ast.parse(path.read_text(encoding="utf8")).body:
        if isinstance(node, ast.Assign) and any(
                isinstance(target, ast.Name) and target.id == "STUBS" for target in node.targets):
            return ast.literal_eval(node.value)
    raise ValueError("Android lifecycle stubs unavailable")


def main() -> None:
    stubs = stubs_from(ROOT / "scripts/analysis/test_android_exit_lifecycle.py")
    stubs.pop("org/majiro/player/ExitLifecycleTest.java")
    stubs["org/majiro/player/HardwareKeyboardTest.java"] = HARNESS
    folder = archive_dir("android_keyboard")
    source, classes = folder / "src", folder / "classes"
    files = []
    for name, text in stubs.items():
        path = source / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf8")
        files.append(path)
    production = [
        ROOT / "player/android/src/org/majiro/player" / name for name in (
            "GameActivity.java", "GameTouchKeys.java", "GameWindowCommands.java")]
    files.extend(production)
    java = Path(read_json(ROOT / "reports/android_toolchain.json")["java"]) / "bin"
    results = {}
    for name, command in (
        ("javac", [java / "javac.exe", "-encoding", "UTF-8", "-source", "8", "-target", "8",
                   "-d", classes, *files]),
        ("jvm", [java / "java.exe", "-cp", classes, "org.majiro.player.HardwareKeyboardTest"]),
        ("sdl", [java / "javap.exe", "-classpath", ROOT / "references/player/SDL3/classes.jar",
                 "-c", "-p", "org.libsdl.app.SDLActivity"]),
    ):
        completed = subprocess.run(list(map(str, command)), capture_output=True, timeout=45,
                                   creationflags=subprocess.CREATE_NO_WINDOW)
        (folder / (folder.name[:8] + "_" + name + ".txt")).write_bytes(completed.stdout + completed.stderr)
        if completed.returncode:
            raise RuntimeError(f"{name} failed; see {folder}")
        results[name] = completed.stdout.decode("utf8", errors="replace")
    # The production SDL surface and Activity share this native keyboard route.
    sdl = results["sdl"]
    handler = sdl[sdl.index("public static boolean handleKeyEvent("):sdl.index("public static android.view.Surface getNativeSurface(")]
    assert "Method onNativeKeyDown:(I)V" in handler and "Method onNativeKeyUp:(I)V" in handler
    assert "Method android/view/KeyEvent.getRepeatCount" not in handler
    overlay_path = ROOT / "player/android/src/org/majiro/player/GameSettingsOverlay.java"
    overlay = overlay_path.read_text(encoding="utf8")
    show = overlay[overlay.index("private void show()"):overlay.index("boolean dismiss()")]
    assert show.index("activity.releaseGameKeys();") < show.index("drawer=new Dialog(activity)")
    assert "boolean isShowing(){return drawer!=null;}" in overlay
    host_path = ROOT / "player/android/game_host.cpp"
    host = host_path.read_text(encoding="utf8")
    native_keys = re.search(r"const int virtual_keys\[\]=\{([^}]+)\}", host)
    assert native_keys is not None
    assert list(map(int, native_keys[1].split(","))) == [13, 27, 37, 38, 39, 40, 33, 34]
    paths = [*production, overlay_path, host_path, ROOT / "references/player/SDL3/classes.jar",
             ROOT / "scripts/analysis/test_android_exit_lifecycle.py"]
    report = {
        "status": "passed", "timestamp": timestamp(),
        "jvm_checks": int(re.search(r"checks: (\d+)", results["jvm"])[1]),
        "source_sha256": {path.relative_to(ROOT).as_posix(): sha256(path) for path in paths},
        "scope": "Real Activity and existing touch queue with deterministic Android/SDL boundaries; bundled SDL bytecode inspected.",
        "physical_keys": "letters, digits, Enter, keypad Enter, Escape, Space, Tab, Backspace, arrows, PageUp/PageDown, Ctrl/Shift/Alt, CapsLock",
        "lifecycle": ["focus loss", "pause", "settings drawer", "exit confirmation", "keyboard removal", "keyboard change", "destroy"],
        "existing_touchscreen_abi": [13, 27, 37, 38, 39, 40, 33, 34],
        "legacy_touchscreen_abi_unchanged": True,
        "touch_panel_test": "test_android_touch_keys",
        "device_accessed": False, "apk_built": False,
        "evidence": str(folder.relative_to(ROOT)),
        "limitations": ["Actual USB/Bluetooth keyboard, focus navigation, IME and modal-window delivery require the unified device check."],
    }
    write_json(ROOT / "reports/android_keyboard_tests.json", report)
    write_json(folder / (folder.name[:8] + "_android_keyboard_tests.json"), report)
    print(f"Hardware keyboard: passed; {report['jvm_checks']} checks; no APK or device operation")


if __name__ == "__main__":
    main()
