package org.majiro.player;

import android.os.Bundle;
import android.content.Intent;
import android.os.Looper;
import android.os.Build;
import android.os.SystemClock;
import android.app.AlertDialog;
import android.hardware.input.InputManager;
import android.view.InputDevice;
import android.view.KeyEvent;
import org.libsdl.app.SDLActivity;
import android.view.WindowManager;
import java.util.HashMap;
import java.util.HashSet;
import java.util.Iterator;
import java.util.Map;

public final class GameActivity extends SDLActivity {
    // Local owner tag: SDL translates the Android key code, not this device id.
    private static final int TOUCH_KEYBOARD_DEVICE=Integer.MIN_VALUE;
    private boolean destroyed;
    private boolean exitRequested;
    private AlertDialog exitDialog;
    private Object backCallback;
    private GameSettingsOverlay settingsOverlay;
    private boolean keyboardPaused,keyboardFocused=true;
    private InputManager keyboardManager;
    private final Map<Long,Integer> keyboardKeys=new HashMap<>();
    private final InputManager.InputDeviceListener keyboardListener=new InputManager.InputDeviceListener(){
        @Override public void onInputDeviceAdded(int device){}
        @Override public void onInputDeviceChanged(int device){releaseKeyboardDevice(device);}
        @Override public void onInputDeviceRemoved(int device){releaseKeyboardDevice(device);}
    };
    private static final class PredictiveBack {
        static Object register(GameActivity owner){
            android.window.OnBackInvokedCallback callback=owner::requestGameBack;
            owner.getOnBackInvokedDispatcher().registerOnBackInvokedCallback(
                android.window.OnBackInvokedDispatcher.PRIORITY_OVERLAY,callback);
            return callback;
        }
        static void unregister(GameActivity owner,Object callback){
            owner.getOnBackInvokedDispatcher().unregisterOnBackInvokedCallback(
                (android.window.OnBackInvokedCallback)callback);
        }
    }
    @Override public void onBackPressed(){requestGameBack();}
    @Override public boolean dispatchKeyEvent(KeyEvent event){
        if(event.getKeyCode()==KeyEvent.KEYCODE_BACK){
            if(event.getAction()==KeyEvent.ACTION_UP&&!event.isCanceled())requestGameBack();
            return true;
        }
        // A cancelled release may no longer carry its original input source.
        final Integer held=event.getAction()==KeyEvent.ACTION_UP?keyboardKeys.get(keyboardOwner(event)):null;
        final int key=held!=null?held:gameKeyboardKey(event);
        if(key!=0)return dispatchGameKeyboard(event,key);
        return super.dispatchKeyEvent(event);
    }
    private static long keyboardOwner(KeyEvent event){
        return ((long)event.getDeviceId()<<32)|(event.getKeyCode()&0xffffffffL);
    }
    private static int gameKeyboardKey(KeyEvent event){
        if((event.getFlags()&KeyEvent.FLAG_SOFT_KEYBOARD)!=0)return 0;
        int source=event.getSource();
        if(source==0&&event.getDevice()!=null)source=event.getDevice().getSources();
        if((source&InputDevice.SOURCE_KEYBOARD)!=InputDevice.SOURCE_KEYBOARD)return 0;
        int key=event.getKeyCode();
        if((key>=KeyEvent.KEYCODE_A&&key<=KeyEvent.KEYCODE_Z)||
           (key>=KeyEvent.KEYCODE_0&&key<=KeyEvent.KEYCODE_9))return key;
        switch(key){
            case KeyEvent.KEYCODE_NUMPAD_ENTER:return KeyEvent.KEYCODE_ENTER;
            case KeyEvent.KEYCODE_ENTER:case KeyEvent.KEYCODE_ESCAPE:
            case KeyEvent.KEYCODE_SPACE:case KeyEvent.KEYCODE_DEL:case KeyEvent.KEYCODE_TAB:
            case KeyEvent.KEYCODE_DPAD_LEFT:case KeyEvent.KEYCODE_DPAD_UP:
            case KeyEvent.KEYCODE_DPAD_RIGHT:case KeyEvent.KEYCODE_DPAD_DOWN:
            case KeyEvent.KEYCODE_PAGE_UP:case KeyEvent.KEYCODE_PAGE_DOWN:
            case KeyEvent.KEYCODE_CTRL_LEFT:case KeyEvent.KEYCODE_CTRL_RIGHT:
            case KeyEvent.KEYCODE_SHIFT_LEFT:case KeyEvent.KEYCODE_SHIFT_RIGHT:
            case KeyEvent.KEYCODE_ALT_LEFT:case KeyEvent.KEYCODE_ALT_RIGHT:
            case KeyEvent.KEYCODE_CAPS_LOCK:return key;
            default:return 0;
        }
    }
    private boolean dispatchGameKeyboard(KeyEvent event,int key){
        final long owner=keyboardOwner(event);
        if(event.getAction()==KeyEvent.ACTION_UP){
            Integer released=keyboardKeys.remove(owner);
            if(released!=null&&!keyboardKeys.containsValue(released))onNativeKeyUp(released);
            return true;
        }
        if(event.getAction()!=KeyEvent.ACTION_DOWN)return true;
        if(destroyed||isFinishing()||exitRequested||exitDialog!=null||keyboardPaused||
           !keyboardFocused||mBrokenLibraries||mSurface==null||event.isCanceled()||
           (settingsOverlay!=null&&settingsOverlay.isShowing()))return true;
        // A repeat after focus restoration must not resurrect a held Ctrl.
        if(event.getRepeatCount()!=0||keyboardKeys.containsKey(owner))return true;
        final boolean held=keyboardKeys.containsValue(key);
        keyboardKeys.put(owner,key);
        // Bypass the floating handle's Android view focus. SDL keeps the same
        // key-down/key-up route and original PC key semantics as its surface.
        if(!held)SDLActivity.handleKeyEvent(mSurface,key,event,null);
        return true;
    }
    boolean sendTouchKey(int key,boolean down,boolean cancelled,long downTime){
        final int flags=cancelled?KeyEvent.FLAG_CANCELED:0;
        KeyEvent event=new KeyEvent(downTime,SystemClock.uptimeMillis(),
            down?KeyEvent.ACTION_DOWN:KeyEvent.ACTION_UP,key,0,0,
            TOUCH_KEYBOARD_DEVICE,0,flags,InputDevice.SOURCE_KEYBOARD);
        dispatchKeyEvent(event);
        return !down||keyboardKeys.containsKey(keyboardOwner(event));
    }
    void releaseGameKeys(){
        if(settingsOverlay!=null)settingsOverlay.releaseTouchKeys();
        for(int key:new HashSet<>(keyboardKeys.values()))onNativeKeyUp(key);
        keyboardKeys.clear();
    }
    private void releaseKeyboardDevice(int device){
        HashSet<Integer> released=new HashSet<>();
        Iterator<Map.Entry<Long,Integer>> entries=keyboardKeys.entrySet().iterator();
        while(entries.hasNext()){
            Map.Entry<Long,Integer> entry=entries.next();
            if((int)(entry.getKey()>>32)==device){released.add(entry.getValue());entries.remove();}
        }
        for(int key:released)if(!keyboardKeys.containsValue(key))onNativeKeyUp(key);
    }
    void requestGameBack(){
        if(Looper.myLooper()!=Looper.getMainLooper()){runOnUiThread(this::requestGameBack);return;}
        if(destroyed||isFinishing()||exitRequested||exitDialog!=null)return;
        if(settingsOverlay!=null&&settingsOverlay.dismiss())return;
        GameBridge.requestOriginalMenu();
    }
    void sendGameKey(int key){
        if(destroyed||isFinishing()||exitRequested||exitDialog!=null)return;
        GameBridge.requestGameKey(key);
    }
    void openWindowMenu(int command){
        if(destroyed||isFinishing()||exitRequested||exitDialog!=null)return;
        GameBridge.requestWindowMenu(command);
    }
    void requestExit(){
        if(Looper.myLooper()!=Looper.getMainLooper()){runOnUiThread(this::requestExit);return;}
        if(settingsOverlay!=null)settingsOverlay.dismiss();
        if(destroyed||isFinishing()||exitRequested||exitDialog!=null)return;
        releaseGameKeys();
        exitDialog=new AlertDialog.Builder(this)
            .setTitle("是否退出当前游戏？")
            .setMessage("退出后返回游戏库。")
            .setNegativeButton("取消",(dialog,which)->{})
            .setPositiveButton("退出",(dialog,which)->{
                exitRequested=true;
                DiagnosticLog.write("game","confirmed exit to library");
                ExternalChecker.cancel();
                ExternalSaveIO.cancel();
                GameBridge.cancelPendingMedia();
                if(mSDLThread!=null&&mSDLThread.isAlive()){
                    nativeSendQuit();
                }else finish();
            }).create();
        exitDialog.setOnDismissListener(dialog->exitDialog=null);
        exitDialog.show();
    }
    void openOriginalMenu(){
        if(destroyed||exitRequested)return;
        GameBridge.requestOriginalMenu();
    }
    @Override public void finish() {
        // SDLMain invokes finish after native main/cleanup on its own thread.
        // Keep Activity teardown on Android's UI thread and this exact owner.
        if(Looper.myLooper()!=Looper.getMainLooper()){
            runOnUiThread(()->{if(!destroyed&&!isFinishing())finish();});
            return;
        }
        if(!destroyed)super.finish();
    }
    @Override protected String[] getLibraries() {
        return new String[] {"SDL3", "majiro_game"};
    }
    @Override protected String[] getArguments() {
        String path=getIntent().getStringExtra("game_path");
        if(path!=null&&!path.isEmpty())return new String[] {path,""};
        String uri=getIntent().getStringExtra("game_tree"),root=getIntent().getStringExtra("game_root");
        if(root!=null&&root.equals(android.provider.DocumentsContract.getTreeDocumentId(android.net.Uri.parse(uri))))root="";
        return new String[] {uri,root==null?"":root};
    }
    @Override protected void onCreate(Bundle state) {
        try {
            DiagnosticLog.initialize(this);
            DiagnosticLog.write("game","launch "+getIntent().getStringExtra("game_title"));
            String path=getIntent().getStringExtra("game_path");
            if(path!=null&&!path.isEmpty()) {
                GameBridge.open(this, "file://" + path, null);
            } else {
                GameBridge.open(this,getIntent().getStringExtra("game_tree"),getIntent().getStringExtra("game_root"));
            }
        } catch (Exception error) {
            // Let the native owner show its normal error dialog and cleanly
            // return to the library instead of crashing during Activity setup.
            GameBridge.launchFailed(error);
        }
        super.onCreate(state);
        settingsOverlay=new GameSettingsOverlay(this);
        keyboardManager=(InputManager)getSystemService(INPUT_SERVICE);
        if(keyboardManager!=null)keyboardManager.registerInputDeviceListener(keyboardListener,null);
        if(Build.VERSION.SDK_INT>=33)backCallback=PredictiveBack.register(this);
        getWindow().addFlags(WindowManager.LayoutParams.FLAG_KEEP_SCREEN_ON);
    }
    @Override protected void onPause() {
        keyboardPaused=true;
        releaseGameKeys();
        GameBridge.pause();
        super.onPause();
    }
    @Override public void onWindowFocusChanged(boolean focused){
        keyboardFocused=focused;
        if(!focused)releaseGameKeys();
        super.onWindowFocusChanged(focused);
    }
    @Override protected void onActivityResult(int request,int result,Intent data){
        if(ExternalChecker.result(request,result,data))return;
        if(ExternalSaveIO.result(this,request,result,data))return;
        super.onActivityResult(request,result,data);
    }
    @Override protected void onResume() {
        super.onResume();
        keyboardPaused=false;
        GameBridge.resume();
    }
    @Override protected void onDestroy() {
        destroyed=true;
        releaseGameKeys();
        if(keyboardManager!=null){
            keyboardManager.unregisterInputDeviceListener(keyboardListener);keyboardManager=null;
        }
        if(settingsOverlay!=null){settingsOverlay.close();settingsOverlay=null;}
        if(Build.VERSION.SDK_INT>=33&&backCallback!=null){
            PredictiveBack.unregister(this,backCallback);backCallback=null;
        }
        if(exitDialog!=null){exitDialog.dismiss();exitDialog=null;}
        ExternalChecker.cancel();
        ExternalSaveIO.cancel();
        super.onDestroy();
        GameBridge.close();
        // This Activity runs in :game. Releasing its finished process gives
        // the next game a fresh SDL/JNI owner while the library stays alive.
        if(isFinishing())android.os.Process.killProcess(android.os.Process.myPid());
    }
}
