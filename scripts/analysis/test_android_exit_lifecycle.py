"""Local production GameActivity lifecycle tests; never controls a device."""
from __future__ import annotations
import json
from pathlib import Path
import re
import subprocess
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "convert"))
from port_common import ROOT, archive_dir, timestamp, write_json, sha256

STUBS = {
"android/os/Process.java": """package android.os;public class Process{
 public static int killed;public static int myPid(){return 1234;}
 public static void killProcess(int pid){if(pid!=1234)throw new AssertionError();killed++;}
}""",
"android/os/Bundle.java": "package android.os; public class Bundle{}",
"android/os/Build.java": "package android.os; public class Build{public static class VERSION{public static int SDK_INT=36;}}",
"android/os/Looper.java": """package android.os; public class Looper{
 public static final Looper MAIN=new Looper();public static boolean ui=true;
 public static Looper getMainLooper(){return MAIN;}public static Looper myLooper(){return ui?MAIN:null;}
}""",
"android/content/Intent.java": """package android.content; public class Intent{
 public String getStringExtra(String name){return name.equals("game_tree")?"content://fixture":"root";}
}""",
"android/net/Uri.java": "package android.net; public class Uri{public static Uri parse(String text){return new Uri();}}",
"android/provider/DocumentsContract.java": """package android.provider;
public class DocumentsContract{public static String getTreeDocumentId(android.net.Uri uri){return "tree";}}""",
"android/view/WindowManager.java": "package android.view; public class WindowManager{public static class LayoutParams{public static int FLAG_KEEP_SCREEN_ON=128;}}",
"android/view/Window.java": "package android.view; public class Window{public void addFlags(int flags){}}",
"android/view/KeyEvent.java": """package android.view;public class KeyEvent{
 public static final int KEYCODE_BACK=4,ACTION_UP=1,ACTION_DOWN=0;private int action;private boolean cancelled;
 public KeyEvent(int action,boolean cancelled){this.action=action;this.cancelled=cancelled;}
 public int getKeyCode(){return KEYCODE_BACK;}public int getAction(){return action;}public boolean isCanceled(){return cancelled;}
}""",
"android/window/OnBackInvokedCallback.java": "package android.window; public interface OnBackInvokedCallback{void onBackInvoked();}",
"android/window/OnBackInvokedDispatcher.java": """package android.window;public class OnBackInvokedDispatcher{
 public static final int PRIORITY_OVERLAY=1000000;public OnBackInvokedCallback callback;public int priority,removed;
 public void registerOnBackInvokedCallback(int priority,OnBackInvokedCallback cb){this.priority=priority;callback=cb;}
 public void unregisterOnBackInvokedCallback(OnBackInvokedCallback cb){if(callback!=cb)throw new AssertionError();callback=null;removed++;}
}""",
"android/content/DialogInterface.java": """package android.content;public interface DialogInterface{
 interface OnClickListener{void onClick(DialogInterface d,int which);}
 interface OnDismissListener{void onDismiss(DialogInterface d);}
}""",
"android/app/AlertDialog.java": """package android.app;import android.content.DialogInterface;
public class AlertDialog implements DialogInterface{
 public static AlertDialog latest;public static int shown;public DialogInterface.OnClickListener yes,no;
 public DialogInterface.OnDismissListener dismiss;public boolean visible;public String title;
 public void setOnDismissListener(DialogInterface.OnDismissListener cb){dismiss=cb;}
 public void show(){visible=true;shown++;latest=this;}
 public void dismiss(){visible=false;if(dismiss!=null)dismiss.onDismiss(this);}
 public void cancel(){if(no!=null)no.onClick(this,0);dismiss();}
 public void confirm(){yes.onClick(this,1);dismiss();}
 public static class Builder{
  private AlertDialog d=new AlertDialog();public Builder(android.app.Activity a){}
  public Builder setTitle(String value){d.title=value;return this;}public Builder setMessage(String value){return this;}
  public Builder setNegativeButton(String value,DialogInterface.OnClickListener cb){d.no=cb;return this;}
  public Builder setPositiveButton(String value,DialogInterface.OnClickListener cb){d.yes=cb;return this;}
  public AlertDialog create(){return d;}
 }
}""",
"android/app/Activity.java": """package android.app;import java.util.ArrayList;
public class Activity{
 public int finishes,pauses,resumes,destroys,superBack;private boolean finishing;
 public final ArrayList<Runnable> queue=new ArrayList<Runnable>();
 public final android.window.OnBackInvokedDispatcher dispatcher=new android.window.OnBackInvokedDispatcher();
 protected void onCreate(android.os.Bundle b){}protected void onPause(){pauses++;}
 protected void onResume(){resumes++;}protected void onDestroy(){destroys++;}
 protected void onActivityResult(int a,int b,android.content.Intent c){}
 public void onBackPressed(){superBack++;finish();}public boolean dispatchKeyEvent(android.view.KeyEvent e){return false;}
 public void finish(){finishes++;finishing=true;}public boolean isFinishing(){return finishing;}
 public boolean isDestroyed(){return destroys>0;}public void runOnUiThread(Runnable r){if(android.os.Looper.ui)r.run();else queue.add(r);}
 public void drain(){android.os.Looper.ui=true;for(Runnable r:new ArrayList<Runnable>(queue))r.run();queue.clear();}
 public android.content.Intent getIntent(){return new android.content.Intent();}
 public android.view.Window getWindow(){return new android.view.Window();}
 public android.window.OnBackInvokedDispatcher getOnBackInvokedDispatcher(){return dispatcher;}
}""",
"org/libsdl/app/SDLActivity.java": """package org.libsdl.app;public class SDLActivity extends android.app.Activity{
 protected static Thread mSDLThread;public static int quit;
 public static void thread(Thread t){mSDLThread=t;}public static void nativeSendQuit(){quit++;}
 protected String[] getLibraries(){return new String[0];}protected String[] getArguments(){return new String[0];}
}""",
"org/majiro/player/GameBridge.java": """package org.majiro.player;
public class GameBridge{
 public static int opens,pauses,resumes,closes,cancelledMedia,menus,keys,commands,lastKey,lastCommand;
 public static RuntimeException openFailure;public static Exception launchError;
 public static void open(android.app.Activity a,String u,String r){opens++;if(openFailure!=null)throw openFailure;}
 public static void launchFailed(Exception error){launchError=error;}
 public static void pause(){pauses++;}public static void resume(){resumes++;}
 public static void close(){closes++;}public static void cancelPendingMedia(){cancelledMedia++;}
 public static void requestOriginalMenu(){menus++;}
 public static void requestGameKey(int key){keys++;lastKey=key;}
 public static void requestWindowMenu(int command){commands++;lastCommand=command;}
}""",
"org/majiro/player/GameSettingsOverlay.java": """package org.majiro.player;
final class GameSettingsOverlay{
 static GameSettingsOverlay latest;boolean showing;int closed;
 GameSettingsOverlay(GameActivity owner){latest=this;}
 boolean dismiss(){boolean was=showing;showing=false;return was;}
 void close(){showing=false;closed++;}
}""",
"org/majiro/player/DiagnosticLog.java": """package org.majiro.player;public class DiagnosticLog{
 public static void initialize(android.app.Activity a){}public static void write(String a,String b){}
}""",
"org/majiro/player/ExternalChecker.java": """package org.majiro.player;public class ExternalChecker{
 public static int cancelled;public static void cancel(){cancelled++;}
 public static boolean result(int a,int b,android.content.Intent c){return false;}
}""",
"org/majiro/player/ExternalSaveIO.java": """package org.majiro.player;public class ExternalSaveIO{
 public static int cancelled;public static void cancel(){cancelled++;}
 public static boolean result(android.app.Activity a,int b,int c,android.content.Intent d){return false;}
}""",
"org/majiro/player/ExitLifecycleTest.java": """package org.majiro.player;
import android.app.AlertDialog;import android.os.*;import android.view.KeyEvent;
import org.libsdl.app.SDLActivity;
public class ExitLifecycleTest{
 static int checks;static void check(boolean value,String why){
  checks++;if(!value)throw new AssertionError(why);
 }
 public static void main(String[]args)throws Exception{
  java.util.concurrent.CountDownLatch stop=new java.util.concurrent.CountDownLatch(1);
  Thread nativeThread=new Thread(()->{
   try{stop.await();}catch(Exception error){throw new RuntimeException(error);}
  });
  nativeThread.start();SDLActivity.thread(nativeThread);
  GameActivity first=new GameActivity();first.onCreate(new Bundle());
  check(first.dispatcher.callback!=null&&first.dispatcher.priority==1000000,"predictive overlay callback");
  first.onBackPressed();
  check(GameBridge.menus==1&&AlertDialog.shown==0&&first.finishes==0&&SDLActivity.quit==0,
        "back sends original menu without requesting exit");
  GameSettingsOverlay overlay=GameSettingsOverlay.latest;overlay.showing=true;
  first.dispatcher.callback.onBackInvoked();
  check(!overlay.showing&&GameBridge.menus==1,"back closes drawer before delivering source input");
  first.dispatchKeyEvent(new KeyEvent(KeyEvent.ACTION_DOWN,false));
  check(GameBridge.menus==1,"back down consumed");
  first.dispatchKeyEvent(new KeyEvent(KeyEvent.ACTION_UP,true));
  check(GameBridge.menus==1,"cancelled back ignored");
  first.dispatchKeyEvent(new KeyEvent(KeyEvent.ACTION_UP,false));
  check(GameBridge.menus==2,"back release reaches original menu");
  first.dispatcher.callback.onBackInvoked();
  check(GameBridge.menus==3,"predictive committed back reaches original menu");
  first.sendGameKey(33);first.openWindowMenu(3);
  check(GameBridge.keys==1&&GameBridge.lastKey==33,"history key routed");
  check(GameBridge.commands==1&&GameBridge.lastCommand==3,"save event routed");
  first.onPause();first.onResume();
  check(GameBridge.pauses==1&&GameBridge.resumes==1&&first.pauses==1&&first.resumes==1,
        "pause/resume reaches both bridge and SDL");
  Looper.ui=false;first.requestExit();
  check(first.queue.size()==1&&AlertDialog.shown==0,"exit creation marshals to UI");
  first.drain();
  check(AlertDialog.shown==1&&first.finishes==0&&SDLActivity.quit==0,"explicit exit asks first");
  first.requestExit();
  check(AlertDialog.shown==1,"no duplicate exit confirmation");
  first.onBackPressed();first.sendGameKey(13);first.openWindowMenu(1);
  check(GameBridge.menus==3&&GameBridge.keys==1&&GameBridge.commands==1,
        "source input blocked while exit confirmation is visible");
  AlertDialog.latest.cancel();
  check(first.finishes==0&&SDLActivity.quit==0&&ExternalChecker.cancelled==0,
        "cancel preserves game and requests");
  first.requestExit();check(AlertDialog.shown==2,"explicit exit can be requested after cancel");
  AlertDialog.latest.confirm();
  check(SDLActivity.quit==1&&first.finishes==0,"native cleanup requested before finish");
  check(GameBridge.cancelledMedia==1,"confirmation wakes pending movie startup");
  first.onBackPressed();first.requestExit();first.sendGameKey(13);first.openWindowMenu(1);
  check(AlertDialog.shown==2&&GameBridge.menus==3&&GameBridge.keys==1&&GameBridge.commands==1,
        "confirmed exit is one-shot and rejects stale input");
  check(ExternalChecker.cancelled==1&&ExternalSaveIO.cancelled==1,
        "pending external requests cancelled after confirmation");
  stop.countDown();nativeThread.join();SDLActivity.thread(null);
  Looper.ui=false;first.finish();
  check(first.finishes==0&&first.queue.size()==1,"native finish marshals to UI");
  first.drain();check(first.finishes==1,"native cleanup completion finishes activity");
  first.onDestroy();
  check(first.dispatcher.callback==null&&first.dispatcher.removed==1,"back callback removed");
  check(overlay.closed==1,"drawer ownership released");
  check(android.os.Process.killed==1,"finished dedicated game process released");
  check(GameBridge.closes==1&&first.destroys==1,"bridge closes after SDL destroy");
  Looper.ui=false;first.finish();first.drain();
  check(first.finishes==1,"stale native callback cannot finish again");
  Build.VERSION.SDK_INT=26;Looper.ui=true;
  GameActivity second=new GameActivity();second.onCreate(new Bundle());
  check(second.dispatcher.callback==null&&GameBridge.opens==2,"fresh legacy owner");
  second.onBackPressed();
  check(GameBridge.menus==4&&AlertDialog.shown==2&&second.finishes==0,"legacy back routes source menu");
  second.requestExit();check(AlertDialog.shown==3,"legacy explicit exit confirms");
  AlertDialog.latest.confirm();
  check(second.finishes==1&&SDLActivity.quit==1,"before native start exits without stale quit");
  second.onDestroy();check(GameBridge.closes==2,"second launch closes its bridge");
  check(android.os.Process.killed==2,"finished game releases its own process");
  GameActivity third=new GameActivity();third.onCreate(new Bundle());third.onDestroy();
  check(android.os.Process.killed==2&&GameBridge.closes==3,"unconfirmed recreation does not kill process");
  third.onBackPressed();third.sendGameKey(13);third.openWindowMenu(1);
  check(GameBridge.menus==4&&GameBridge.keys==1&&GameBridge.commands==1,"destroyed owner rejects input");
  check(first.superBack==0&&second.superBack==0,"SDL/platform immediate back never called");
  GameBridge.openFailure=new IllegalStateException("text preflight rejected");
  GameActivity rejected=new GameActivity();rejected.onCreate(new Bundle());
  check(GameBridge.launchError==GameBridge.openFailure,"startup failure handed to native error owner");
  check(rejected.finishes==0&&rejected.dispatcher.callback==null,"failed legacy launch reaches SDL setup");
  rejected.finish();rejected.onDestroy();
  check(GameBridge.closes==4&&android.os.Process.killed==3,"failed launch still closes its bridge on exit");
  GameBridge.openFailure=null;
  System.out.println("GameActivity lifecycle checks: "+checks);
 }
}""",
}


def main():
    tools = json.loads((ROOT / "reports/android_toolchain.json").read_text(encoding="utf8"))
    java = Path(tools["java"]) / "bin"
    folder = archive_dir("android_exit_lifecycle")
    source = folder / "src"
    classes = folder / "classes"
    files = []
    for name, text in STUBS.items():
        path = source / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf8")
        files.append(path)
    production = ROOT / "player/android/src/org/majiro/player/GameActivity.java"
    files.append(production)
    def run(name, command):
        result = subprocess.run(list(map(str, command)), capture_output=True,
                                creationflags=subprocess.CREATE_NO_WINDOW)
        (folder / ("20261110_" + name + ".txt")).write_bytes(result.stdout + result.stderr)
        if result.returncode:
            raise RuntimeError(name + " failed: " + str(folder))
        return result.stdout.decode("utf8", "replace")
    run("javac", [java / "javac.exe", "-encoding", "UTF-8", "-source", "8", "-target", "8",
                  "-d", classes, *files])
    output = run("jvm", [java / "java.exe", "-cp", classes, "org.majiro.player.ExitLifecycleTest"])
    sdl = run("sdl_javap", [java / "javap.exe", "-classpath", ROOT / "references/player/SDL3/classes.jar",
                            "-c", "-p", "org.libsdl.app.SDLMain"])
    assert sdl.index("nativeCleanupMainThread") < sdl.index("SDLActivity.finish")
    sdl_activity = run("sdl_activity_javap", [
        java / "javap.exe", "-classpath", ROOT / "references/player/SDL3/classes.jar",
        "-c", "-p", "org.libsdl.app.SDLActivity"])
    assert "registerOnBackInvokedCallback" not in sdl_activity
    host = (ROOT / "player/android/game_host.cpp").read_text(encoding="utf8")
    import xml.etree.ElementTree as ET
    manifest=ET.parse(ROOT/"player/android/AndroidManifest.xml").getroot()
    android="{http://schemas.android.com/apk/res/android}"
    game=next(node for node in manifest.findall("./application/activity")if node.get(android+"name")==".GameActivity")
    assert game.get(android+"process")==":game","process cleanup must never target the library process"
    boot = (ROOT / "player/runtime/player_boot.lua").read_text(encoding="utf8")
    system = (ROOT / "player/runtime/majiro_syscalls.lua").read_text(encoding="utf8")
    terminal = host.index('std::strcmp(status,"finished")==0')
    assert terminal < host.index("presentation.draw(renderer", terminal)
    assert "quit=true;break;" in host[terminal:terminal + 250]
    assert host.index('SDL_SetHint(SDL_HINT_ANDROID_ALLOW_RECREATE_ACTIVITY,"1")')<host.index("need(SDL_Init(")
    assert "PLAYER_STOP"not in host and "function PLAYER_STOP"not in boot
    assert host.count("NewGlobalRef") == 1 and host.count("DeleteGlobalRef(bridge)") == 1
    assert host.index("lua_close(lua)") < host.index("DeleteGlobalRef(bridge)") < host.index("bridge=nullptr;env=nullptr")
    reset_fields = ("movie_rgba={}", "movie_page=0", "movie_sequence=-1", "movie_texture_mode=false",
                    "movie_surface_active=false", "movie_clear_background=false", "screen_width=screen_height=0",
                    "root_uri.clear()", "sources.clear()", "image_cache.clear()", "audio_cache.clear()",
                    "password.clear()", "key_down.clear()", "debug_clients.clear()", "patch_layers.clear()")
    cleanup = host[host.index("lua_close(lua)"): ]
    assert all(value in cleanup for value in reset_fields)
    system_end = system[system.index("define('$system_end'"):]
    assert system_end.index("saves.flush(vm)") < system_end.index("vm.status='finished'")
    external_quit_flush = "PLAYER_SHUTDOWN" in host and "function PLAYER_SHUTDOWN" in boot
    shutdown_call=host[host.index('lua_getglobal(lua,"PLAYER_SHUTDOWN")'):host.index("lua_close(lua)")]
    assert "lua_pcall" in shutdown_call and "lua_pop(lua,1)" in shutdown_call
    quit_at=host.index("if(quit)")
    quit_branch=host[quit_at:host.index("if (paused)",quit_at)]
    assert "break" in quit_branch
    from lua_bridge import create_lua
    lua=create_lua()
    shutdown=boot[boot.index("function PLAYER_SHUTDOWN()"):boot.index("function PLAYER_DEBUG()")]
    lua.globals()["SHUTDOWN_SOURCE"]=shutdown
    lua_checks=lua.execute("""
      local commits,stops=0,0
      local state={persist_dirty=true,media={channels={music={id=1},voice={id=2}}}}
      local vm={status='waiting'}
      local system={state=state,saves={flush=function(owner)
        assert(owner==vm);assert(state.persist_dirty);commits=commits+1;state.persist_dirty=false
      end}}
      local HOST={audio=function(action,spec)assert(action=='stop');stops=stops+1 end,
        movie=function()end,translation=function()end}
      local online,options=nil,{}
      local install=assert(loadstring('return function(system,vm,HOST,options,online)\\n'..
        SHUTDOWN_SOURCE..'\\nreturn PLAYER_SHUTDOWN end'))()
      local stop=install(system,vm,HOST,options,online)
      stop();assert(commits==1 and stops==2 and state.shutdown_requested and next(state.media.channels)==nil)
      stop();assert(commits==1 and stops==2)
      state.shutdown_requested=true;stop();assert(commits==1)
      local failed={persist_dirty=true,media={channels={a={id=1},b={id=2}}}}
      local audio,movie,translation=0,0,0
      local broken=install({state=failed,saves={flush=function()error('disk write failed')end}},vm,{
        audio=function()audio=audio+1;error('decoder release failed')end,
        movie=function()movie=movie+1 end,translation=function()translation=translation+1 end},{},nil)
      local ok,why=pcall(broken)
      assert(not ok and why:find('disk write failed',1,true)and why:find('decoder release failed',1,true))
      assert(audio==2 and movie==1 and translation==1 and failed.shutdown_requested)
      assert(pcall(broken))
      return 6
    """)
    result = {
        "schema": 1, "timestamp": timestamp(), "status": "passed" if external_quit_flush else "partial",
        "jvm_checks": int(re.search(r"checks: (\d+)", output)[1]),
        "sdl_native_cleanup_before_finish": True, "sdl_has_own_predictive_callback": False,
        "finished_stops_native_loop_before_render": True, "jni_global_ref_ownership_balanced": True,
        "native_reentry_fields_reset": list(reset_fields), "script_system_end_flushes_before_finished": True,
        "confirmed_os_quit_flush_hook_present": external_quit_flush,
        "lua_flush_idempotence_checks": lua_checks,
        "remaining": [] if external_quit_flush else ["Root must add PLAYER_SHUTDOWN and call it before Lua close on SDL quit."],
        "scope": "production GameActivity JVM with Android/SDL boundaries; source contracts plus bundled SDL bytecode",
        "device_accessed": False, "android_runtime_verified": False,
        "source_sha256": {name: sha256(ROOT / name) for name in (
            "player/android/src/org/majiro/player/GameActivity.java", "player/android/game_host.cpp",
            "player/runtime/player_boot.lua", "player/runtime/majiro_syscalls.lua",
            "references/player/SDL3/classes.jar")}, "evidence": str(folder)}
    write_json(ROOT / "reports/android_exit_lifecycle_tests.json", result)
    write_json(folder / "20261110_android_exit_lifecycle.json", result)
    print(output.strip(), "flush hook:", external_quit_flush)


if __name__ == "__main__":
    main()
