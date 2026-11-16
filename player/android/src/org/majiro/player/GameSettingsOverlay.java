package org.majiro.player;

import android.app.Dialog;
import android.content.SharedPreferences;
import android.os.SystemClock;
import android.view.Gravity;
import android.view.KeyEvent;
import android.view.MotionEvent;
import android.view.View;
import android.view.ViewConfiguration;
import android.view.ViewGroup;
import android.view.Window;
import android.widget.FrameLayout;
import android.widget.LinearLayout;
import android.widget.ScrollView;
import android.widget.TextView;
import android.widget.Toast;
import java.util.ArrayList;
import java.util.concurrent.ExecutorService;
import java.util.concurrent.Executors;

/** In-app edge handle; no system overlay permission or external window owner. */
final class GameSettingsOverlay {
    private final GameActivity activity;
    private final ExecutorService worker=Executors.newSingleThreadExecutor();
    private final FrameLayout layer;
    private final TextView ball;
    private final SharedPreferences preferences;
    private final int size;
    private boolean right=true,dragging,closed;
    private float downX,downY,startX,startY,fraction=.35f;
    private Dialog drawer;
    private TextView status;
    private LinearLayout touchPanel;
    private boolean touchKeysEnabled;
    private final ArrayList<TouchKey> touchKeys=new ArrayList<>();
    private final Runnable update=new Runnable(){
        @Override public void run(){
            if(closed||drawer==null)return;
            status.setText(modeLabel(GameBridge.translationMode())+"\n"+
                TranslationService.call(activity,"status",""));
            status.postDelayed(this,1000);
        }
    };

    GameSettingsOverlay(GameActivity activity){
        this.activity=activity;
        preferences=activity.getSharedPreferences("game-overlay",0);
        right=preferences.getBoolean("right",true);fraction=preferences.getFloat("position",.35f);
        size=Ui.dp(activity,44);
        Ui.Colors colors=Ui.colors(activity);
        layer=new FrameLayout(activity);
        layer.setClipChildren(false);layer.setMotionEventSplittingEnabled(true);
        ball=Ui.text(activity,"≡",24,colors.onAccent,true);
        ball.setGravity(Gravity.CENTER);ball.setAlpha(.68f);ball.setElevation(Ui.dp(activity,8));
        ball.setBackground(Ui.ripple(activity,colors,colors.accent,22,false));
        ball.setContentDescription("游戏设置，拖动可移动，点击展开");
        ball.setFocusable(true);
        layer.addView(ball,new FrameLayout.LayoutParams(size,size));
        activity.addContentView(layer,new ViewGroup.LayoutParams(-1,-1));
        layer.addOnLayoutChangeListener((v,l,t,r,b,ol,ot,or,ob)->{
            if(!dragging)snap(false);fitTouchPanel();
        });
        ball.setOnClickListener(v->show());
        ball.setOnTouchListener((view,event)->touch(event));
        layer.post(()->snap(false));
        setTouchKeysVisible(preferences.getBoolean("touch-keys",false));
    }
    private final class TouchKey implements View.OnTouchListener,View.OnClickListener {
        final TextView view;
        final int key;
        int pointer=-1;
        long downTime;
        boolean touchClick;
        TouchKey(TextView view,int key){this.view=view;this.key=key;}
        void release(boolean cancelled){
            if(pointer<0)return;
            pointer=-1;view.setPressed(false);
            activity.sendTouchKey(key,false,cancelled,downTime);
        }
        @Override public boolean onTouch(View owner,MotionEvent event){
            switch(event.getActionMasked()){
                case MotionEvent.ACTION_DOWN:
                    if(closed||!touchKeysEnabled)return true;
                    release(true);
                    pointer=event.getPointerId(event.getActionIndex());
                    downTime=event.getDownTime();view.setPressed(true);
                    if(!activity.sendTouchKey(key,true,false,downTime)){
                        pointer=-1;view.setPressed(false);
                    }
                    return true;
                case MotionEvent.ACTION_MOVE:
                    int index=event.findPointerIndex(pointer);
                    if(pointer>=0&&(index<0||event.getX(index)<0||event.getY(index)<0||
                       event.getX(index)>=view.getWidth()||event.getY(index)>=view.getHeight()))release(true);
                    return true;
                case MotionEvent.ACTION_POINTER_UP:
                    if(event.getPointerId(event.getActionIndex())==pointer)release(false);
                    return true;
                case MotionEvent.ACTION_UP:
                    boolean clicked=pointer>=0;
                    release(false);
                    if(clicked){
                        touchClick=true;
                        try{view.performClick();}finally{touchClick=false;}
                    }
                    return true;
                case MotionEvent.ACTION_CANCEL:release(true);return true;
                default:return true;
            }
        }
        @Override public void onClick(View owner){
            if(touchClick||pointer>=0||closed||!touchKeysEnabled)return;
            long now=SystemClock.uptimeMillis();
            activity.sendTouchKey(key,true,false,now);
            activity.sendTouchKey(key,false,false,now);
        }
    }
    private void touchKey(LinearLayout parent,String label,String description,int key){
        Ui.Colors colors=Ui.colors(activity);
        TextView button=Ui.text(activity,label,14,colors.ink,true);
        button.setGravity(Gravity.CENTER);button.setMinHeight(Ui.dp(activity,48));
        button.setPadding(Ui.dp(activity,4),Ui.dp(activity,4),Ui.dp(activity,4),Ui.dp(activity,4));
        button.setBackground(Ui.ripple(activity,colors,colors.surface,10,true));
        button.setContentDescription(description);button.setFocusable(true);
        TouchKey control=new TouchKey(button,key);touchKeys.add(control);
        button.setOnTouchListener(control);button.setOnClickListener(control);
        LinearLayout.LayoutParams params=new LinearLayout.LayoutParams(0,-2,1);
        params.setMargins(Ui.dp(activity,2),Ui.dp(activity,2),Ui.dp(activity,2),Ui.dp(activity,2));
        parent.addView(button,params);
    }
    private void createTouchPanel(){
        Ui.Colors colors=Ui.colors(activity);
        touchPanel=new LinearLayout(activity);touchPanel.setOrientation(LinearLayout.VERTICAL);
        touchPanel.setPadding(Ui.dp(activity,6),Ui.dp(activity,4),Ui.dp(activity,6),Ui.dp(activity,6));
        touchPanel.setBackground(Ui.outlined(activity,colors,colors.surface,14));
        touchPanel.setAlpha(.9f);touchPanel.setElevation(Ui.dp(activity,8));
        touchPanel.setClickable(true);touchPanel.setMotionEventSplittingEnabled(true);
        LinearLayout header=new LinearLayout(activity);header.setGravity(Gravity.CENTER_VERTICAL);
        TextView title=Ui.text(activity,"触屏按键",12,colors.muted,true);
        title.setPadding(Ui.dp(activity,6),0,0,0);header.addView(title,new LinearLayout.LayoutParams(0,-2,1));
        TextView hide=Ui.button(activity,"收起",colors,false);
        hide.setOnClickListener(v->setTouchKeysVisible(false));header.addView(hide);
        touchPanel.addView(header);
        LinearLayout top=new LinearLayout(activity),bottom=new LinearLayout(activity);
        touchKey(top,"↑","方向键上",KeyEvent.KEYCODE_DPAD_UP);
        touchKey(top,"回车","回车键，确认",KeyEvent.KEYCODE_ENTER);
        touchKey(top,"空格","空格键",KeyEvent.KEYCODE_SPACE);
        touchKey(top,"Ctrl\n按住快进","按住 Ctrl 快进，松开停止",KeyEvent.KEYCODE_CTRL_LEFT);
        touchKey(bottom,"←","方向键左",KeyEvent.KEYCODE_DPAD_LEFT);
        touchKey(bottom,"↓","方向键下",KeyEvent.KEYCODE_DPAD_DOWN);
        touchKey(bottom,"→","方向键右",KeyEvent.KEYCODE_DPAD_RIGHT);
        touchKey(bottom,"Shift","Shift 键，按住生效",KeyEvent.KEYCODE_SHIFT_LEFT);
        touchPanel.addView(top);touchPanel.addView(bottom);
        FrameLayout.LayoutParams params=new FrameLayout.LayoutParams(Ui.dp(activity,336),-2,Gravity.BOTTOM|Gravity.LEFT);
        params.setMargins(Ui.dp(activity,8),Ui.dp(activity,8),Ui.dp(activity,8),Ui.dp(activity,8));
        layer.addView(touchPanel,params);fitTouchPanel();
    }
    private void fitTouchPanel(){
        if(touchPanel==null||layer.getWidth()<=0)return;
        int width=Math.min(Ui.dp(activity,336),Math.max(1,layer.getWidth()-Ui.dp(activity,16)));
        ViewGroup.LayoutParams params=touchPanel.getLayoutParams();
        if(params.width!=width){params.width=width;touchPanel.setLayoutParams(params);}
    }
    private void setTouchKeysVisible(boolean visible){
        if(closed)return;
        touchKeysEnabled=visible;
        if(visible&&touchPanel==null)createTouchPanel();
        if(!visible)releaseTouchKeys();
        if(touchPanel!=null)touchPanel.setVisibility(visible?View.VISIBLE:View.GONE);
        if(preferences.getBoolean("touch-keys",false)!=visible)
            preferences.edit().putBoolean("touch-keys",visible).apply();
    }
    void releaseTouchKeys(){
        for(TouchKey key:touchKeys)key.release(true);
    }
    private boolean touch(MotionEvent event){
        switch(event.getActionMasked()){
            case MotionEvent.ACTION_DOWN:
                ball.animate().cancel();downX=event.getRawX();downY=event.getRawY();
                startX=ball.getX();startY=ball.getY();dragging=false;ball.setAlpha(1);return true;
            case MotionEvent.ACTION_MOVE:
                float dx=event.getRawX()-downX,dy=event.getRawY()-downY;
                if(Math.hypot(dx,dy)>ViewConfiguration.get(activity).getScaledTouchSlop())dragging=true;
                if(dragging){
                    ball.setX(Math.max(0,Math.min(layer.getWidth()-size,startX+dx)));
                    ball.setY(Math.max(0,Math.min(layer.getHeight()-size,startY+dy)));
                }
                return true;
            case MotionEvent.ACTION_UP:
            case MotionEvent.ACTION_CANCEL:
                boolean clicked=!dragging&&event.getActionMasked()==MotionEvent.ACTION_UP;
                if(dragging){
                    right=ball.getX()+size/2f>=layer.getWidth()/2f;
                    fraction=ball.getY()/Math.max(1,layer.getHeight()-size);
                    preferences.edit().putBoolean("right",right).putFloat("position",fraction).apply();
                }
                dragging=false;snap(true);if(clicked)ball.performClick();return true;
            default:return false;
        }
    }
    private void snap(boolean animate){
        if(layer.getWidth()<=0||layer.getHeight()<=0)return;
        float x=right?layer.getWidth()-size+Ui.dp(activity,8):-Ui.dp(activity,8);
        float y=Math.max(0,Math.min(1,fraction))*Math.max(0,layer.getHeight()-size);
        if(animate)ball.animate().x(x).y(y).alpha(.68f).setDuration(180).start();
        else{ball.setX(x);ball.setY(y);}
    }
    private static String modeLabel(String mode){
        return mode.equals("online")?"在线翻译已开启":mode.equals("local")?"本地汉化":
            mode.equals("original")?"日语原文":"自动";
    }
    private void row(LinearLayout parent,String title,Runnable action){
        Ui.Colors colors=Ui.colors(activity);
        TextView button=Ui.button(activity,title,colors,false);
        LinearLayout.LayoutParams params=new LinearLayout.LayoutParams(-1,-2);
        params.setMargins(0,Ui.dp(activity,10),0,0);parent.addView(button,params);
        button.setOnClickListener(v->action.run());
    }
    private void changed(){
        String requested=TranslationSettings.load(activity).mode;
        if(GameBridge.applyTextMode(requested)){
            Toast.makeText(activity,"翻译设置已应用，后续文本生效",Toast.LENGTH_SHORT).show();
        }else new android.app.AlertDialog.Builder(activity).setTitle("重新进入游戏后生效")
            .setMessage("本地汉化与原文使用不同的脚本和存档。设置已保存，返回游戏库后重新进入即可应用。")
            .setNegativeButton("继续当前游戏",null)
            .setPositiveButton("返回游戏库",(d,w)->activity.requestExit()).show();
        if(drawer!=null){status.removeCallbacks(update);update.run();}
    }
    private void show(){
        if(closed||drawer!=null||activity.isFinishing())return;
        activity.releaseGameKeys();
        Ui.Colors colors=Ui.colors(activity);
        drawer=new Dialog(activity);drawer.requestWindowFeature(Window.FEATURE_NO_TITLE);
        ScrollView scroll=new ScrollView(activity);
        LinearLayout panel=new LinearLayout(activity);panel.setOrientation(LinearLayout.VERTICAL);
        panel.setPadding(Ui.dp(activity,22),Ui.dp(activity,22),Ui.dp(activity,22),Ui.dp(activity,26));
        panel.setBackgroundColor(colors.surface);scroll.addView(panel);
        TextView title=Ui.text(activity,"游戏设置",23,colors.ink,true);Ui.heading(title);panel.addView(title);
        status=Ui.text(activity,"",14,colors.muted,false);
        status.setPadding(0,Ui.dp(activity,12),0,Ui.dp(activity,8));panel.addView(status);
        row(panel,touchKeysEnabled?"隐藏触屏按键":"显示触屏按键（方向／空格／Ctrl）",()->{
            dismiss();setTouchKeysVisible(!touchKeysEnabled);
        });
        row(panel,"文本与翻译设置",()->TranslationSettingsDialog.show(activity,worker,this::changed));
        row(panel,"开启／关闭在线翻译",()->{
            TranslationSettings saved=TranslationSettings.load(activity);
            String next=GameBridge.translationMode().equals("online")?"original":"online";
            if(next.equals("online")&&(saved.url.isEmpty()||saved.model.isEmpty())){
                TranslationSettingsDialog.show(activity,worker,this::changed);return;
            }
            try{TranslationSettings.save(activity,next,saved.url,saved.key,saved.model,saved.context);changed();}
            catch(Exception error){Toast.makeText(activity,"未能保存翻译设置",Toast.LENGTH_LONG).show();}
        });
        row(panel,"游戏内保存",()->{dismiss();activity.openWindowMenu(3);});
        row(panel,"游戏内读取",()->{dismiss();activity.openWindowMenu(1);});
        row(panel,"游戏内设置",()->{dismiss();activity.openWindowMenu(99);});
        row(panel,"返回上一层（右键）",()->{dismiss();activity.openOriginalMenu();});
        row(panel,"发送 Esc（部分游戏返回）",()->{dismiss();activity.sendGameKey(27);});
        row(panel,"确认当前选项（回车）",()->{dismiss();activity.sendGameKey(13);});
        row(panel,"查看对话历史（PageUp）",()->{dismiss();activity.sendGameKey(33);});
        row(panel,"历史向后翻页（PageDown）",()->{dismiss();activity.sendGameKey(34);});
        row(panel,"返回游戏",this::dismiss);
        row(panel,"退出到游戏库",()->{dismiss();activity.requestExit();});
        drawer.setContentView(scroll);
        drawer.setOnDismissListener(d->{if(status!=null)status.removeCallbacks(update);drawer=null;});
        drawer.show();
        Window window=drawer.getWindow();
        if(window!=null){
            window.setBackgroundDrawableResource(android.R.color.transparent);
            window.addFlags(android.view.WindowManager.LayoutParams.FLAG_DIM_BEHIND);
            window.setDimAmount(.35f);window.setGravity(right?Gravity.RIGHT:Gravity.LEFT);
            window.setLayout(Math.min(Ui.dp(activity,340),activity.getResources().getDisplayMetrics().widthPixels),
                ViewGroup.LayoutParams.MATCH_PARENT);
            window.setWindowAnimations(android.R.style.Animation_Dialog);
        }
        update.run();
    }
    boolean dismiss(){
        if(drawer==null)return false;
        drawer.dismiss();return true;
    }
    boolean isShowing(){return drawer!=null;}
    void close(){
        closed=true;releaseTouchKeys();dismiss();worker.shutdownNow();
        if(layer.getParent()instanceof ViewGroup)((ViewGroup)layer.getParent()).removeView(layer);
    }
}
