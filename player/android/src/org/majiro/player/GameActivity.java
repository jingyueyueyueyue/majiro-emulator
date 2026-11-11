package org.majiro.player;

import android.os.Bundle;
import android.content.Intent;
import android.os.Looper;
import android.os.Build;
import android.app.AlertDialog;
import android.view.KeyEvent;
import org.libsdl.app.SDLActivity;
import android.view.WindowManager;

public final class GameActivity extends SDLActivity {
    private boolean destroyed;
    private boolean exitRequested;
    private AlertDialog exitDialog;
    private Object backCallback;
    private GameSettingsOverlay settingsOverlay;
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
        return super.dispatchKeyEvent(event);
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
        String uri=getIntent().getStringExtra("game_tree"),root=getIntent().getStringExtra("game_root");
        if(root!=null&&root.equals(android.provider.DocumentsContract.getTreeDocumentId(android.net.Uri.parse(uri))))root="";
        return new String[] {uri,root==null?"":root};
    }
    @Override protected void onCreate(Bundle state) {
        try {
            DiagnosticLog.initialize(this);
            DiagnosticLog.write("game","launch "+getIntent().getStringExtra("game_title"));
            GameBridge.open(this,getIntent().getStringExtra("game_tree"),getIntent().getStringExtra("game_root"));
        } catch (Exception error) {
            // Let the native owner show its normal error dialog and cleanly
            // return to the library instead of crashing during Activity setup.
            GameBridge.launchFailed(error);
        }
        super.onCreate(state);
        settingsOverlay=new GameSettingsOverlay(this);
        if(Build.VERSION.SDK_INT>=33)backCallback=PredictiveBack.register(this);
        getWindow().addFlags(WindowManager.LayoutParams.FLAG_KEEP_SCREEN_ON);
    }
    @Override protected void onPause() {
        GameBridge.pause();
        super.onPause();
    }
    @Override protected void onActivityResult(int request,int result,Intent data){
        if(ExternalChecker.result(request,result,data))return;
        if(ExternalSaveIO.result(this,request,result,data))return;
        super.onActivityResult(request,result,data);
    }
    @Override protected void onResume() {
        super.onResume();
        GameBridge.resume();
    }
    @Override protected void onDestroy() {
        destroyed=true;
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
