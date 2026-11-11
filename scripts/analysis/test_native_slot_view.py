"""Execute the actual Android picker view against strict JVM boundaries."""
from pathlib import Path
import json
import subprocess
import sys

ROOT=Path(__file__).resolve().parents[2]
sys.path[:0]=[str(ROOT/"scripts"/n)for n in("convert","build")]
from port_common import read_json,write_json,archive_dir,timestamp,sha256
from run_background_compatibility import source_identity
JAVA_SOURCE=ROOT/"player/android/src/org/majiro/player"

STUBS={
"android/util/DisplayMetrics.java":"""package android.util;public class DisplayMetrics{
 public int heightPixels=1200;public float density=1;}""",
"android/content/res/Resources.java":"""package android.content.res;public class Resources{
 public android.util.DisplayMetrics getDisplayMetrics(){return new android.util.DisplayMetrics();}}""",
"android/content/Context.java":"""package android.content;public class Context{
 public android.content.res.Resources getResources(){return new android.content.res.Resources();}}""",
"android/app/Activity.java":"""package android.app;public class Activity extends android.content.Context{}""",
"android/content/DialogInterface.java":"""package android.content;public interface DialogInterface{
 interface OnClickListener{void onClick(DialogInterface d,int which);}
 interface OnShowListener{void onShow(DialogInterface d);}}""",
"android/R.java":"""package android;public class R{public static class layout{
 public static final int simple_spinner_dropdown_item=1,simple_list_item_single_choice=2;}}""",
"android/view/View.java":"""package android.view;public class View{
 public static final int VISIBLE=0,GONE=8;public boolean enabled=true;public int visibility=VISIBLE;
 public interface OnClickListener{void onClick(View v);}public OnClickListener click;
 public void setOnClickListener(OnClickListener c){click=c;}public void setEnabled(boolean e){enabled=e;}
 public boolean isEnabled(){return enabled;}public void setVisibility(int v){visibility=v;}
 public int getVisibility(){return visibility;}public void setPadding(int a,int b,int c,int d){}
 public void setContentDescription(String s){}public boolean performClick(){
 if(!enabled||click==null)return false;click.onClick(this);return true;}}""",
"android/view/ViewGroup.java":"""package android.view;public class ViewGroup extends View{
 public static class LayoutParams{public LayoutParams(int w,int h){}}
 public final java.util.ArrayList<View> children=new java.util.ArrayList<View>();
 public void addView(View v){children.add(v);}public void addView(View v,LayoutParams p){children.add(v);}
 public View getChildAt(int n){return children.get(n);}}""",
"android/widget/TextView.java":"""package android.widget;public class TextView extends android.view.View{
 public CharSequence text="";public TextView(android.content.Context c){}
 public void setText(CharSequence s){text=s;}public CharSequence getText(){return text;}}""",
"android/widget/LinearLayout.java":"""package android.widget;public class LinearLayout extends android.view.ViewGroup{
 public static final int VERTICAL=1;public LinearLayout(android.content.Context c){}
 public void setOrientation(int n){}public static class LayoutParams extends android.view.ViewGroup.LayoutParams{
 public LayoutParams(int w,int h){super(w,h);}}}""",
"android/widget/ArrayAdapter.java":"""package android.widget;public class ArrayAdapter<T>{
 public final java.util.List<T> values;public ArrayAdapter(android.content.Context c,int r,java.util.List<T> v){values=v;}
 public void notifyDataSetChanged(){}public int getCount(){return values.size();}
 public T getItem(int n){return values.get(n);}}""",
"android/widget/AdapterView.java":"""package android.widget;public class AdapterView<T> extends android.view.ViewGroup{
 public interface OnItemSelectedListener{void onItemSelected(AdapterView<?>p,android.view.View v,int n,long id);
 void onNothingSelected(AdapterView<?>p);}
 public interface OnItemClickListener{void onItemClick(AdapterView<?>p,android.view.View v,int n,long id);}}""",
"android/widget/Spinner.java":"""package android.widget;public class Spinner extends AdapterView<Object>{
 public ArrayAdapter<?>adapter;public OnItemSelectedListener listener;public int position;
 public Spinner(android.content.Context c){}public void setAdapter(ArrayAdapter<?>a){adapter=a;}
 public void setOnItemSelectedListener(OnItemSelectedListener l){listener=l;l.onItemSelected(this,null,position,position);}
 public void setSelection(int n){if(n<0||n>=adapter.getCount())throw new AssertionError("spinner range");
 position=n;if(listener!=null)listener.onItemSelected(this,null,n,n);}}""",
"android/widget/ListView.java":"""package android.widget;public class ListView extends AdapterView<Object>{
 public static final int CHOICE_MODE_SINGLE=1;public ArrayAdapter<?>adapter;public OnItemClickListener listener;public int checked=-1;
 public ListView(android.content.Context c){}public void setChoiceMode(int n){}public void setAdapter(ArrayAdapter<?>a){adapter=a;}
 public void setOnItemClickListener(OnItemClickListener l){listener=l;}public void clearChoices(){checked=-1;}
 public void setItemChecked(int n,boolean value){if(n<0||n>=adapter.getCount())throw new AssertionError("item range");checked=value?n:-1;}
 public void choose(int n){if(n<0||n>=adapter.getCount())throw new AssertionError("item range");listener.onItemClick(this,null,n,n);}}""",
"android/graphics/Bitmap.java":"""package android.graphics;public class Bitmap{}""",
"android/graphics/drawable/Drawable.java":"""package android.graphics.drawable;public class Drawable{}""",
"android/widget/ImageView.java":"""package android.widget;public class ImageView extends android.view.View{
 public enum ScaleType{FIT_CENTER}public android.graphics.Bitmap bitmap;
 public ImageView(android.content.Context c){}public void setAdjustViewBounds(boolean b){}public void setScaleType(ScaleType s){}
 public void setImageDrawable(android.graphics.drawable.Drawable d){bitmap=null;}
 public void setImageBitmap(android.graphics.Bitmap b){bitmap=b;}}""",
"android/app/AlertDialog.java":"""package android.app;public class AlertDialog implements android.content.DialogInterface{
 public static final int BUTTON_POSITIVE=-1,BUTTON_NEGATIVE=-2;
 public android.view.View view;public boolean shown;public String title;
 private android.content.DialogInterface.OnShowListener onShow;
 private android.content.DialogInterface.OnClickListener negative;
 private final android.widget.TextView positive=new android.widget.TextView(new android.content.Context());
 private final android.widget.TextView cancel=new android.widget.TextView(new android.content.Context());
 public static class Builder{private final AlertDialog dialog=new AlertDialog();
 public Builder(Activity a){}public Builder setTitle(String t){dialog.title=t;return this;}
 public Builder setView(android.view.View v){dialog.view=v;return this;}public Builder setCancelable(boolean b){return this;}
 public Builder setPositiveButton(String s,android.content.DialogInterface.OnClickListener l){return this;}
 public Builder setNegativeButton(String s,android.content.DialogInterface.OnClickListener l){dialog.negative=l;return this;}
 public AlertDialog create(){return dialog;}}
 public void setOnShowListener(android.content.DialogInterface.OnShowListener l){onShow=l;}
 public void show(){shown=true;cancel.setOnClickListener(v->{negative.onClick(this,BUTTON_NEGATIVE);dismiss();});
 if(onShow!=null)onShow.onShow(this);}public void dismiss(){shown=false;}
 public android.widget.TextView getButton(int n){if(!shown)return null;return n==BUTTON_POSITIVE?positive:cancel;}
}""",

"org/majiro/player/Ui.java":"""package org.majiro.player;final class Ui{
 static class Colors{int ink=1,muted=2;}static Colors colors(android.content.Context c){return new Colors();}
 static int dp(android.content.Context c,float n){return(int)n;}
 static android.widget.TextView text(android.content.Context c,String s,float z,int color,boolean bold){
 android.widget.TextView v=new android.widget.TextView(c);v.setText(s);return v;}}""",
}

HARNESS='package org.majiro.player;\nimport android.app.*;import android.widget.*;import org.json.*;\npublic class NativeSlotViewChecks{\n static int checks,sequence,previews;\n static void check(boolean b){checks++;if(!b)throw new AssertionError("check "+checks);}\n static ModalDialogState state;static ModalDialogState.Request request;\n static AlertDialog open(boolean loading,int...ids){\n  state=new ModalDialogState();state.foreground(true);\n  sequence++;JSONArray rows=new JSONArray();\n  for(int id:ids)rows.put(new JSONObject().put("id",id).put("time","time").put("info","record=1\\nslot=0\\nname")\n   .put("thumbnail","thumb_fixture.png"));\n  String payload=new JSONObject().put("schema",1).put("loading",loading).put("normal_min",101)\n   .put("normal_max",1100).put("page_size",100).put("current_slot",101).put("entries",rows).toString();\n  request=state.begin("token",327681,payload,"title");\n  return show();\n }\n static AlertDialog show(){return NativeSlotDialog.show(new Activity(),request,state,name->{\n  check(name.equals("thumb_fixture.png"));previews++;return new android.graphics.Bitmap();});}\n static LinearLayout panel(AlertDialog d){return(LinearLayout)d.view;}\n static ListView list(AlertDialog d){return(ListView)panel(d).getChildAt(2);}\n static Spinner pages(AlertDialog d){return(Spinner)panel(d).getChildAt(0);}\n public static void main(String[]args){\n  AlertDialog d=open(true);check(!d.getButton(-1).isEnabled());check(list(d).adapter.getCount()==0);\n  check(!d.getButton(-1).performClick());check(state.poll("token").equals("pending"));\n  d.getButton(-2).performClick();check(state.poll("token").equals("data "));\n  d=open(true,205);check(!d.getButton(-1).isEnabled());check(list(d).adapter.getCount()==1);\n  check(list(d).adapter.getItem(0).toString().contains("record=1\\nslot=0"));\n  list(d).choose(0);check(d.getButton(-1).isEnabled());\n  check(((TextView)panel(d).getChildAt(3)).getText().toString().contains("record=1\\nslot=0"));\n  check(((ImageView)panel(d).getChildAt(4)).bitmap!=null&&previews>0);\n  d.getButton(-1).performClick();check(state.poll("token").equals("data 736c6f743d323035"));\n  d=open(true,10001,10003);\n  check(list(d).adapter.getItem(0).toString().startsWith("自动存档"));\n  check(list(d).adapter.getItem(1).toString().startsWith("快速存档 1"));\n  list(d).choose(0);d.getButton(-1).performClick();\n  check(state.poll("token").equals("data 736c6f743d3130303031"));\n  d=open(true,10001,10003);list(d).choose(1);d.getButton(-1).performClick();\n  check(state.poll("token").equals("data 736c6f743d3130303033"));\n  d=open(false);check(!d.getButton(-1).isEnabled());check(list(d).adapter.getCount()==100);\n  list(d).choose(0);d.getButton(-1).performClick();check(state.poll("token").equals("data 736c6f743d313031"));\n  d=open(false);pages(d).setSelection(9);list(d).choose(99);d.getButton(-1).performClick();\n  check(state.poll("token").equals("data 736c6f743d31313030"));\n  d=open(true,205);list(d).choose(0);check(request.draft.equals("slot=205"));\n  state.foreground(false);check(state.visible()==null);d.dismiss();state.foreground(true);\n  d=show();check(d.getButton(-1).isEnabled()&&list(d).checked==0);\n  d.getButton(-1).performClick();check(state.poll("token").equals("data 736c6f743d323035"));\n  d=open(true,205);list(d).choose(0);state.cancel("token");d.getButton(-1).performClick();\n  check(d.shown);ModalDialogState.Request fresh=state.begin("fresh",327681,"another","title");\n  state.completeData(request,"slot=205");check(state.poll("fresh").equals("pending"));\n  try{open(true,205,205);throw new AssertionError("duplicate metadata accepted");}\n  catch(IllegalArgumentException expected){check(true);}\n  try{open(true,0);throw new AssertionError("invalid metadata accepted");}\n  catch(IllegalArgumentException expected){check(true);}\n  System.out.println("{\\"status\\":\\"passed\\",\\"checks\\":"+checks+"}");\n }\n}\n'


def main():
    folder=archive_dir("native_slot_view")
    before=source_identity();source=folder/"src";classes=folder/"classes"
    files=[]
    for name,text in STUBS.items():
        path=source/name;path.parent.mkdir(parents=True,exist_ok=True);path.write_text(text,encoding="utf8");files.append(path)
    path=source/"org/majiro/player/NativeSlotViewChecks.java";path.write_text(HARNESS,encoding="utf8");files.append(path)
    files.extend(JAVA_SOURCE/name for name in("NativeSlotSelection.java","ModalDialogState.java","NativeSlotDialog.java"))
    java=Path(read_json(ROOT/"reports/android_toolchain.json")["java"])/"bin"
    jar=ROOT/"references/player/testing-json/json-20240303.jar"
    assert sha256(jar)=="3cf6cd6892e32e2b4c1c39e0f52f5248a2f5b37646fdfbb79a66b46b618414ed"
    compile=subprocess.run([str(java/"javac.exe"),"-encoding","UTF-8","-source","8","-target","8",
        "-cp",str(jar),"-d",str(classes),*map(str,files)],cwd=ROOT,capture_output=True,timeout=45)
    (folder/"javac.txt").write_bytes(compile.stdout+compile.stderr)
    assert compile.returncode==0,(compile.stdout+compile.stderr).decode(errors="replace")
    result=subprocess.run([str(java/"java.exe"),"-cp",str(classes)+";"+str(jar),"org.majiro.player.NativeSlotViewChecks"],
        cwd=ROOT,capture_output=True,timeout=15)
    (folder/"checks.txt").write_bytes(result.stdout+result.stderr)
    assert result.returncode==0,(result.stdout+result.stderr).decode(errors="replace")
    report=json.loads(result.stdout);report.update(timestamp=timestamp(),production_integrated=True,
        source_unchanged=before==source_identity(),evidence=str(folder.relative_to(ROOT)),device_accessed=False,
        scope="actual NativeSlotDialog view event handlers, selection model and async replies with strict Android API stubs and pinned real JSON parsing",
        limits=["Android rendering/layout/physical touch is not executed",
                "Standalone org.json validates common JSON syntax; actual Android parser/rendering remain device acceptance work"])
    write_json(folder/"report.json",report);write_json(ROOT/"reports/native_slot_view.json",report)
    print(folder);print(report["checks"],"production view interaction checks passed")


if __name__=="__main__":main()
