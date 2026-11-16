"""Run real touch controls and Activity against deterministic Android UI boundaries."""
from __future__ import annotations

import os
from pathlib import Path
import re
import subprocess
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "convert"))
from port_common import ROOT, archive_dir, read_json, sha256, timestamp, write_json
from test_android_keyboard import stubs_from


UI_STUBS = {
"android/R.java": """package android;public class R{
 public static class color{public static int transparent=0;}
 public static class style{public static int Animation_Dialog=1;}
}""",
"android/content/SharedPreferences.java": """package android.content;import java.util.*;
public class SharedPreferences{
 public final Map<String,Object> values=new HashMap<>();
 public boolean getBoolean(String key,boolean fallback){return values.containsKey(key)?(Boolean)values.get(key):fallback;}
 public float getFloat(String key,float fallback){return values.containsKey(key)?(Float)values.get(key):fallback;}
 public Editor edit(){return new Editor();}
 public class Editor{
  public Editor putBoolean(String key,boolean value){values.put(key,value);return this;}
  public Editor putFloat(String key,float value){values.put(key,value);return this;}
  public void apply(){}
 }
}""",
"android/content/res/Resources.java": """package android.content.res;public class Resources{
 public android.util.DisplayMetrics getDisplayMetrics(){return new android.util.DisplayMetrics();}
}""",
"android/util/DisplayMetrics.java": "package android.util;public class DisplayMetrics{public int widthPixels=1280;}",
"android/view/Gravity.java": """package android.view;public class Gravity{
 public static final int CENTER=17,CENTER_VERTICAL=16,RIGHT=5,LEFT=3,BOTTOM=80;
}""",
"android/view/ViewConfiguration.java": """package android.view;public class ViewConfiguration{
 public static ViewConfiguration get(Object owner){return new ViewConfiguration();}public int getScaledTouchSlop(){return 8;}
}""",
"android/view/MotionEvent.java": """package android.view;public class MotionEvent{
 public static final int ACTION_DOWN=0,ACTION_UP=1,ACTION_MOVE=2,ACTION_CANCEL=3,ACTION_POINTER_DOWN=5,ACTION_POINTER_UP=6;
 private final int action,index;private final int[] ids;private final float[] x,y;
 public MotionEvent(int action,int index,int[] ids,float[] x,float[] y){this.action=action;this.index=index;this.ids=ids;this.x=x;this.y=y;}
 public int getActionMasked(){return action;}public int getActionIndex(){return index;}
 public int getPointerId(int index){return ids[index];}
 public int findPointerIndex(int id){for(int i=0;i<ids.length;i++)if(ids[i]==id)return i;return -1;}
 public float getRawX(){return x[0];}public float getRawY(){return y[0];}
 public float getX(int index){return x[index];}public float getY(int index){return y[index];}
 public long getDownTime(){return 100;}
}""",
"android/view/View.java": """package android.view;public class View{
 public static final int VISIBLE=0,GONE=8;public Object parent;public int width=80,height=60,visibility=0;
 private float x,y,alpha=1;private boolean pressed;private CharSequence description;
 private ViewGroup.LayoutParams layout;
 public interface OnClickListener{void onClick(View view);}
 public interface OnTouchListener{boolean onTouch(View view,MotionEvent event);}
 public interface OnLayoutChangeListener{void onLayoutChange(View view,int l,int t,int r,int b,int ol,int ot,int or,int ob);}
 private OnClickListener click;private OnTouchListener touch;
 public void setOnClickListener(OnClickListener listener){click=listener;}
 public void setOnTouchListener(OnTouchListener listener){touch=listener;}
 public void addOnLayoutChangeListener(OnLayoutChangeListener listener){}
 public boolean performClick(){if(click==null)return false;click.onClick(this);return true;}
 public boolean dispatchTouchEvent(MotionEvent event){return touch!=null&&touch.onTouch(this,event);}
 public Object getParent(){return parent;}
 public void setPressed(boolean value){pressed=value;}public boolean isPressed(){return pressed;}
 public void setContentDescription(CharSequence value){description=value;}public CharSequence getContentDescription(){return description;}
 public void setFocusable(boolean value){}public void setClickable(boolean value){}
 public void setMinHeight(int height){}public void setPadding(int l,int t,int r,int b){}
 public void setBackground(Object background){}public void setBackgroundColor(int color){}public void setElevation(float elevation){}
 public float getX(){return x;}public float getY(){return y;}public void setX(float x){this.x=x;}public void setY(float y){this.y=y;}
 public void setAlpha(float alpha){this.alpha=alpha;}public int getWidth(){return width;}public int getHeight(){return height;}
 public void setVisibility(int value){visibility=value;}public int getVisibility(){return visibility;}
 public void setLayoutParams(ViewGroup.LayoutParams params){layout=params;}public ViewGroup.LayoutParams getLayoutParams(){return layout;}
 public void post(Runnable callback){callback.run();}public void postDelayed(Runnable callback,long delay){}public void removeCallbacks(Runnable callback){}
 public ViewPropertyAnimator animate(){return new ViewPropertyAnimator(this);}
}""",
"android/view/ViewPropertyAnimator.java": """package android.view;public class ViewPropertyAnimator{
 private final View view;public ViewPropertyAnimator(View view){this.view=view;}
 public void cancel(){}public void start(){}
 public ViewPropertyAnimator x(float value){view.setX(value);return this;}
 public ViewPropertyAnimator y(float value){view.setY(value);return this;}
 public ViewPropertyAnimator alpha(float value){view.setAlpha(value);return this;}
 public ViewPropertyAnimator setDuration(long duration){return this;}
}""",
"android/view/ViewGroup.java": """package android.view;import java.util.*;public class ViewGroup extends View{
 public final List<View> children=new ArrayList<>();
 public void setMotionEventSplittingEnabled(boolean enabled){}
 public void addView(View view){addView(view,new LayoutParams(-2,-2));}
 public void addView(View view,LayoutParams params){view.parent=this;view.setLayoutParams(params);children.add(view);}
 public void removeView(View view){children.remove(view);view.parent=null;}
 public int getChildCount(){return children.size();}public View getChildAt(int index){return children.get(index);}
 public static class LayoutParams{public static final int MATCH_PARENT=-1;public int width,height;public LayoutParams(int width,int height){this.width=width;this.height=height;}}
}""",
"android/view/Window.java": """package android.view;public class Window{
 public static final int FEATURE_NO_TITLE=1;
 public void addFlags(int flags){}public void setBackgroundDrawableResource(int resource){}
 public void setDimAmount(float value){}public void setGravity(int value){}public void setLayout(int width,int height){}
 public void setWindowAnimations(int value){}
}""",
"android/view/WindowManager.java": """package android.view;public class WindowManager{
 public static class LayoutParams{public static final int FLAG_KEEP_SCREEN_ON=128,FLAG_DIM_BEHIND=2;}
}""",
"android/widget/FrameLayout.java": """package android.widget;public class FrameLayout extends android.view.ViewGroup{
 public FrameLayout(Object owner){width=1280;height=720;}public void setClipChildren(boolean value){}
 public static class LayoutParams extends android.view.ViewGroup.LayoutParams{
  public LayoutParams(int width,int height){super(width,height);}public LayoutParams(int width,int height,int gravity){super(width,height);}
  public void setMargins(int l,int t,int r,int b){}
 }
}""",
"android/widget/LinearLayout.java": """package android.widget;public class LinearLayout extends android.view.ViewGroup{
 public static final int VERTICAL=1;public LinearLayout(Object owner){}public void setOrientation(int orientation){}
 public void setGravity(int gravity){}
 public static class LayoutParams extends android.view.ViewGroup.LayoutParams{
  public LayoutParams(int width,int height){super(width,height);}public LayoutParams(int width,int height,float weight){super(width,height);}
  public void setMargins(int l,int t,int r,int b){}
 }
}""",
"android/widget/ScrollView.java": "package android.widget;public class ScrollView extends FrameLayout{public ScrollView(Object owner){super(owner);}}",
"android/widget/TextView.java": """package android.widget;public class TextView extends android.view.View{
 private CharSequence text="";public TextView(Object owner){}public void setText(CharSequence value){text=value;}public CharSequence getText(){return text;}
 public void setGravity(int gravity){}
}""",
"android/widget/Toast.java": """package android.widget;public class Toast{
 public static final int LENGTH_SHORT=0,LENGTH_LONG=1;public static Toast makeText(Object owner,String text,int duration){return new Toast();}
 public void show(){}
}""",
"android/app/Dialog.java": """package android.app;import android.content.DialogInterface;public class Dialog implements DialogInterface{
 public static Dialog latest;public android.view.View content;private DialogInterface.OnDismissListener listener;
 public Dialog(Object owner){latest=this;}public void requestWindowFeature(int feature){}
 public void setContentView(android.view.View view){content=view;}public void setOnDismissListener(DialogInterface.OnDismissListener listener){this.listener=listener;}
 public void show(){}public void dismiss(){if(listener!=null){DialogInterface.OnDismissListener value=listener;listener=null;value.onDismiss(this);}}
 public android.view.Window getWindow(){return new android.view.Window();}
}""",
"org/majiro/player/Ui.java": """package org.majiro.player;import android.widget.TextView;public class Ui{
 static class Colors{int onAccent=1,accent=2,surface=3,ink=4,muted=5;}
 static Colors colors(Object owner){return new Colors();}
 static int dp(Object owner,float value){return (int)value;}
 static TextView text(Object owner,String label,float size,int color,boolean bold){TextView view=new TextView(owner);view.setText(label);return view;}
 static TextView button(Object owner,String label,Colors colors,boolean accent){return text(owner,label,14,0,true);}
 static Object ripple(Object owner,Colors colors,int color,float radius,boolean outline){return new Object();}
 static Object outlined(Object owner,Colors colors,int color,float radius){return new Object();}
 static void heading(android.view.View view){}
}""",
"org/majiro/player/TranslationSettings.java": """package org.majiro.player;public class TranslationSettings{
 public String mode="original",url="",model="",key="";public int context;
 public static TranslationSettings load(GameActivity activity){return new TranslationSettings();}
 public static void save(GameActivity activity,String mode,String url,String key,String model,int context)throws Exception{}
}""",
"org/majiro/player/TranslationSettingsDialog.java": """package org.majiro.player;public class TranslationSettingsDialog{
 public static void show(GameActivity activity,java.util.concurrent.ExecutorService worker,Runnable changed){}
}""",
"org/majiro/player/TranslationService.java": """package org.majiro.player;public class TranslationService{
 public static String call(GameActivity activity,String command,String value){return "";}
}""",
}


HARNESS = r"""package org.majiro.player;
import android.app.*;
import android.os.Bundle;
import android.view.*;
import android.widget.*;
import org.libsdl.app.SDLActivity;
import java.util.*;
public class TouchKeysTest {
 static int checks;
 static void check(boolean value,String why){checks++;if(!value)throw new AssertionError(why);}
 static void expect(String... events){
  check(SDLActivity.keyEvents.equals(Arrays.asList(events)),"edges "+SDLActivity.keyEvents+" expected "+Arrays.toString(events));
  SDLActivity.keyEvents.clear();
 }
 static void released(int... keys){
  ArrayList<String> want=new ArrayList<>(),actual=new ArrayList<>(SDLActivity.keyEvents);
  for(int key:keys)want.add("up:"+key);Collections.sort(want);Collections.sort(actual);
  check(actual.equals(want),"held keys release once "+actual);SDLActivity.keyEvents.clear();
 }
 static View find(View root,String label){
  if(label.equals(String.valueOf(root.getContentDescription()))||
     root instanceof TextView&&label.equals(((TextView)root).getText().toString()))return root;
  if(root instanceof ViewGroup)for(View child:((ViewGroup)root).children){View found=find(child,label);if(found!=null)return found;}
  return null;
 }
 static View key(GameActivity game,String label){
  View found=find(game.content,label);check(found!=null,"touch key exists "+label);return found;
 }
 static MotionEvent event(int action,int id,float x,float y){
  return new MotionEvent(action,0,new int[]{id},new float[]{x},new float[]{y});
 }
 static void touch(View key,int action,int id){check(key.dispatchTouchEvent(event(action,id,5,5)),"touch consumed");}
 static void enable(GameActivity game){
  key(game,"游戏设置，拖动可移动，点击展开").performClick();
  View row=find(Dialog.latest.content,"显示触屏按键（方向／空格／Ctrl）");
  check(row!=null,"settings exposes touch-panel toggle");row.performClick();
 }
 static GameActivity launch(){
  GameActivity game=new GameActivity();game.onCreate(new Bundle());game.onResume();game.onWindowFocusChanged(true);return game;
 }
 public static void main(String[] args)throws Exception{
  GameActivity game=launch();
  check(find(game.content,"方向键上")==null,"touch panel starts optional");
  enable(game);expect();
  String[] labels={"方向键上","回车键，确认","空格键","按住 Ctrl 快进，松开停止","方向键左","方向键下","方向键右","Shift 键，按住生效"};
  int[] keys={19,66,62,113,21,20,22,59};
  for(int i=0;i<labels.length;i++){
   View control=key(game,labels[i]);
   touch(control,0,1);check(control.isPressed(),"pressed visual follows down");expect("down:"+keys[i]);
   touch(control,1,1);check(!control.isPressed(),"pressed visual clears on release");expect("up:"+keys[i]);
   control.performClick();expect("down:"+keys[i],"up:"+keys[i]);
   touch(control,0,2);touch(control,3,2);expect("down:"+keys[i],"up:"+keys[i]);
   check(!control.isPressed(),"cancel clears pressed visual");
  }
  View ctrl=key(game,labels[3]),space=key(game,labels[2]),shift=key(game,labels[7]),left=key(game,labels[4]);
  touch(ctrl,0,1);touch(ctrl,2,1);touch(ctrl,2,1);expect("down:113");
  touch(space,0,2);touch(space,1,2);expect("down:62","up:62");
  touch(ctrl,1,1);expect("up:113");
  touch(ctrl,0,3);touch(shift,0,4);expect("down:113","down:59");
  touch(ctrl,3,3);expect("up:113");touch(shift,1,4);expect("up:59");
  touch(ctrl,0,5);
  ctrl.dispatchTouchEvent(event(2,5,-1,5));expect("down:113","up:113");
  touch(ctrl,2,5);touch(ctrl,1,5);expect();
  touch(ctrl,0,5);ctrl.dispatchTouchEvent(event(2,99,5,5));expect("down:113","up:113");
  touch(ctrl,1,99);expect();
  touch(ctrl,0,5);
  ctrl.dispatchTouchEvent(new MotionEvent(5,1,new int[]{5,6},new float[]{5,5},new float[]{5,5}));
  ctrl.dispatchTouchEvent(new MotionEvent(6,1,new int[]{5,6},new float[]{5,5},new float[]{5,5}));
  expect("down:113");touch(ctrl,1,5);expect("up:113");
  touch(ctrl,0,5);
  ctrl.dispatchTouchEvent(new MotionEvent(6,0,new int[]{5,6},new float[]{5,5},new float[]{5,5}));
  expect("down:113","up:113");touch(ctrl,1,6);expect();

  game.dispatchKeyEvent(new KeyEvent(0,113,7,257,0,0,false));touch(ctrl,0,1);expect("down:113");
  touch(ctrl,1,1);expect();
  game.dispatchKeyEvent(new KeyEvent(1,113,7,257,0,0,false));expect("up:113");
  touch(ctrl,0,1);game.dispatchKeyEvent(new KeyEvent(0,113,7,257,0,0,false));expect("down:113");
  game.dispatchKeyEvent(new KeyEvent(1,113,7,257,0,0,false));expect();touch(ctrl,1,1);expect("up:113");
  game.dispatchKeyEvent(new KeyEvent(0,113,7,257,0,0,false));touch(ctrl,0,1);expect("down:113");
  game.inputManager.listener.onInputDeviceRemoved(7);expect();touch(ctrl,1,1);expect("up:113");
  game.dispatchKeyEvent(new KeyEvent(0,113,7,257,0,0,false));touch(ctrl,0,1);expect("down:113");
  key(game,"收起").performClick();expect();
  game.dispatchKeyEvent(new KeyEvent(1,113,7,257,0,0,false));expect("up:113");enable(game);

  touch(ctrl,0,1);touch(shift,0,2);touch(space,0,3);expect("down:113","down:59","down:62");
  game.onWindowFocusChanged(false);released(113,59,62);
  check(!ctrl.isPressed()&&!shift.isPressed()&&!space.isPressed(),"focus loss clears touch ownership");
  game.onWindowFocusChanged(true);touch(ctrl,2,1);touch(ctrl,1,1);expect();
  touch(ctrl,0,1);touch(shift,0,2);expect("down:113","down:59");
  game.onPause();released(113,59);game.onResume();touch(ctrl,2,1);touch(ctrl,1,1);expect();
  touch(ctrl,0,1);expect("down:113");
  key(game,"游戏设置，拖动可移动，点击展开").performClick();expect("up:113");
  check(!ctrl.isPressed(),"opening drawer cancels held touchscreen Ctrl");
  touch(ctrl,0,2);check(!ctrl.isPressed(),"blocked touch cannot retain pressed state");touch(ctrl,1,2);expect();
  find(Dialog.latest.content,"返回游戏").performClick();touch(ctrl,2,2);touch(ctrl,1,2);expect();

  touch(ctrl,0,1);expect("down:113");key(game,"收起").performClick();expect("up:113");
  check(!ctrl.isPressed(),"hiding panel releases keys");
  touch(ctrl,0,2);touch(ctrl,1,2);ctrl.performClick();expect();
  enable(game);touch(left,0,3);touch(left,1,3);expect("down:21","up:21");
  touch(ctrl,0,1);touch(space,0,2);expect("down:113","down:62");game.requestExit();released(113,62);
  touch(ctrl,0,3);touch(ctrl,1,3);expect();AlertDialog.latest.cancel();
  touch(ctrl,2,3);touch(ctrl,1,3);expect();
  touch(ctrl,0,4);expect("down:113");game.requestExit();expect("up:113");
  AlertDialog.latest.confirm();touch(ctrl,0,5);touch(ctrl,1,5);expect();
  game.onDestroy();expect();
  check(game.content.children.isEmpty(),"overlay layer detached");
  check(GameBridge.keys==0&&GameBridge.commands==0,"touch holds use SDL rather than one-shot bridge");
  check(GameTouchKeys.consume()==0,"hold path does not leak into old eight-key protocol");
  GameActivity restored=launch();check(find(restored.content,"方向键上")!=null,"panel preference restored");
  View restoredCtrl=key(restored,labels[3]);touch(restoredCtrl,0,1);expect("down:113");
  restored.onDestroy();expect("up:113");restoredCtrl.performClick();expect();
  System.out.println("Touch controls checks: "+checks);
 }
}"""


def main() -> None:
    stubs = stubs_from(ROOT / "scripts/analysis/test_android_exit_lifecycle.py")
    stubs.pop("org/majiro/player/ExitLifecycleTest.java")
    stubs.pop("org/majiro/player/GameSettingsOverlay.java")
    activity = stubs["android/app/Activity.java"]
    activity = activity.replace("public class Activity{", """public class Activity{
 public final android.widget.FrameLayout content=new android.widget.FrameLayout(this);
 public static final android.content.SharedPreferences preferences=new android.content.SharedPreferences();
 public android.content.SharedPreferences getSharedPreferences(String name,int mode){return preferences;}
 public android.content.res.Resources getResources(){return new android.content.res.Resources();}
 public void addContentView(android.view.View view,android.view.ViewGroup.LayoutParams params){content.addView(view,params);}
""")
    stubs["android/app/Activity.java"] = activity
    stubs["android/app/AlertDialog.java"] = stubs["android/app/AlertDialog.java"].replace(
        "public AlertDialog create(){return d;}", "public AlertDialog create(){return d;}public AlertDialog show(){d.show();return d;}")
    stubs["org/majiro/player/GameBridge.java"] = stubs["org/majiro/player/GameBridge.java"].replace(
        "public static void requestOriginalMenu()", """public static String translationMode(){return "original";}
 public static boolean applyTextMode(String mode){return true;}
 public static void requestOriginalMenu()""")
    stubs.update(UI_STUBS)
    stubs["org/majiro/player/TouchKeysTest.java"] = HARNESS
    folder = archive_dir("android_touch_keys")
    files = []
    for name, text in stubs.items():
        path = folder / "src" / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf8")
        files.append(path)
    production = [ROOT / "player/android/src/org/majiro/player" / name for name in (
        "GameActivity.java", "GameSettingsOverlay.java", "GameTouchKeys.java", "GameWindowCommands.java")]
    files.extend(production)
    tools = read_json(ROOT / "reports/android_toolchain.json")
    java, sdk = Path(tools["java"]) / "bin", Path(tools["sdk"])
    classes = folder / "classes"
    results = {}

    def execute(name: str, command: list) -> None:
        completed = subprocess.run(list(map(str, command)), capture_output=True, timeout=45,
                                   creationflags=subprocess.CREATE_NO_WINDOW)
        (folder / (folder.name[:8] + "_" + name + ".txt")).write_bytes(completed.stdout + completed.stderr)
        if completed.returncode:
            raise RuntimeError(f"{name} failed; see {folder}")
        results[name] = completed.stdout.decode("utf8", errors="replace")

    execute("javac", [java / "javac.exe", "-encoding", "UTF-8", "-source", "8", "-target", "8", "-d", classes, *files])
    execute("jvm", [java / "java.exe", "-cp", classes, "org.majiro.player.TouchKeysTest"])
    # Check the same production files against real Android/SDL API signatures.
    sdk_stubs = [path for path in files[:-len(production)]
                 if path.parent.name == "player" and path.name not in ("Ui.java", "TouchKeysTest.java")]
    appearance = folder / "sdk_src/org/majiro/player/AppearanceSettings.java"
    appearance.parent.mkdir(parents=True, exist_ok=True)
    appearance.write_text("package org.majiro.player;class AppearanceSettings{static boolean isDark(android.content.Context owner){return false;}}", encoding="utf8")
    execute("android_api_javac", [
        java / "javac.exe", "-encoding", "UTF-8", "-source", "8", "-target", "8",
        "-bootclasspath", os.pathsep.join(map(str, (sdk / "platforms/android-36/android.jar",
            sdk / "build-tools/36.0.0/core-lambda-stubs.jar"))),
        "-classpath", ROOT / "references/player/SDL3/classes.jar",
        "-d", folder / "sdk_classes", *production,
        ROOT / "player/android/src/org/majiro/player/Ui.java", *sdk_stubs, appearance,
    ])
    report = {
        "status": "passed", "timestamp": timestamp(),
        "jvm_checks": int(re.search(r"checks: (\d+)", results["jvm"])[1]),
        "android_sdk_api_compile": "passed", "new_jni_protocol": False,
        "scope": "Real GameActivity and GameSettingsOverlay UI handlers with deterministic multi-pointer events; targeted Android/SDL API compilation.",
        "touch_keys": ["Up", "Enter", "Space", "Ctrl", "Left", "Down", "Right", "Shift"],
        "release_cases": ["up", "cancel", "slide outside", "missing pointer", "primary pointer up",
                          "hide", "focus loss", "pause", "drawer", "exit confirmation", "destroy"],
        "source_sha256": {path.relative_to(ROOT).as_posix(): sha256(path) for path in
                          [*production, ROOT / "scripts/analysis/test_android_exit_lifecycle.py", Path(__file__)]},
        "evidence": str(folder.relative_to(ROOT)), "device_accessed": False, "apk_built": False,
        "limitations": ["Real Android hit testing, multitouch delivery across overlay and SDL surface, button geometry and game pacing require unified tablet acceptance."],
    }
    write_json(ROOT / "reports/android_touch_keys_tests.json", report)
    write_json(folder / (folder.name[:8] + "_android_touch_keys_tests.json"), report)
    print(f"Touch controls: passed; {report['jvm_checks']} JVM checks; Android API compile passed; no APK")


if __name__ == "__main__":
    main()
