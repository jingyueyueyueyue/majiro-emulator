package org.majiro.player;

import android.app.Activity;
import android.app.AlertDialog;
import android.graphics.Bitmap;
import android.view.View;
import android.widget.AdapterView;
import android.widget.ArrayAdapter;
import android.widget.ImageView;
import android.widget.LinearLayout;
import android.widget.ListView;
import android.widget.Spinner;
import android.widget.TextView;
import java.util.ArrayList;
import java.util.HashMap;
import org.json.JSONArray;
import org.json.JSONObject;

/** Source slot picker with explicit selection, metadata and saved-frame preview. */
final class NativeSlotDialog {
    interface Preview { Bitmap read(String filename); }
    private static final class Record {
        final int id;
        final String time,info,thumbnail;
        Record(JSONObject json)throws Exception{
            id=json.getInt("id");time=json.getString("time");
            info=json.getString("info");thumbnail=json.getString("thumbnail");
            if(!thumbnail.isEmpty()&&!thumbnail.matches("thumb_[A-Za-z0-9_-]{1,100}\\.png"))
                throw new IllegalArgumentException("native thumbnail filename");
        }
    }
    private NativeSlotDialog(){}

    static AlertDialog show(Activity activity,ModalDialogState.Request request,
                            ModalDialogState state,Preview preview){
        try{
            JSONObject data=new JSONObject(request.message);
            if(data.getInt("schema")!=1||data.getInt("normal_min")!=101
                    ||data.getInt("normal_max")!=1100||data.getInt("page_size")!=100)
                throw new IllegalArgumentException("native slot schema");
            boolean loading=data.getBoolean("loading");
            JSONArray entries=data.getJSONArray("entries");
            HashMap<Integer,Record> records=new HashMap<Integer,Record>();
            ArrayList<Integer> ids=new ArrayList<Integer>();
            for(int i=0;i<entries.length();i++){
                Record record=new Record(entries.getJSONObject(i));
                if(records.put(record.id,record)!=null)
                    throw new IllegalArgumentException("duplicate native slot");
                ids.add(record.id);
            }
            int[] available=new int[ids.size()];
            for(int i=0;i<available.length;i++)available[i]=ids.get(i);
            NativeSlotSelection selection=new NativeSlotSelection(loading,available);
            selection.restoreDraft(request.draft);
            Ui.Colors colors=Ui.colors(activity);
            LinearLayout panel=new LinearLayout(activity);
            panel.setOrientation(LinearLayout.VERTICAL);
            int padding=Ui.dp(activity,16);panel.setPadding(padding,padding,padding,padding);
            Spinner pages=new Spinner(activity);
            ArrayList<String> pageNames=new ArrayList<String>();
            if(loading)pageNames.add("全部存档");
            for(int i=0;i<10;i++)pageNames.add(String.format(java.util.Locale.ROOT,"第 %02d 页",i+1));
            if(loading)pageNames.add("快速／自动存档");
            pages.setAdapter(new ArrayAdapter<String>(activity,android.R.layout.simple_spinner_dropdown_item,pageNames));
            panel.addView(pages,new LinearLayout.LayoutParams(-1,-2));
            TextView empty=Ui.text(activity,"暂无可读取的存档",15,colors.muted,false);
            panel.addView(empty);
            ListView list=new ListView(activity);list.setChoiceMode(ListView.CHOICE_MODE_SINGLE);
            panel.addView(list,new LinearLayout.LayoutParams(-1,
                Math.max(Ui.dp(activity,120),Math.min(Ui.dp(activity,240),
                    activity.getResources().getDisplayMetrics().heightPixels/3))));
            TextView detail=Ui.text(activity,"请选择存档位置",14,colors.ink,false);
            detail.setPadding(0,padding,0,Ui.dp(activity,8));panel.addView(detail);
            ImageView image=new ImageView(activity);image.setAdjustViewBounds(true);
            image.setScaleType(ImageView.ScaleType.FIT_CENTER);
            image.setContentDescription("选中存档的游戏截图");
            panel.addView(image,new LinearLayout.LayoutParams(-1,Ui.dp(activity,130)));
            AlertDialog dialog=new AlertDialog.Builder(activity).setTitle(loading?"读取游戏":"保存游戏")
                .setView(panel).setCancelable(false)
                .setPositiveButton(loading?"读取":"保存",null)
                .setNegativeButton("取消",(owner,which)->{
                    if(state.owns(request)){selection.cancel();state.completeData(request,"");}
                }).create();
            ArrayList<Integer> visible=new ArrayList<Integer>();
            ArrayList<String> labels=new ArrayList<String>();
            ArrayAdapter<String> adapter=new ArrayAdapter<String>(activity,
                android.R.layout.simple_list_item_single_choice,labels);
            list.setAdapter(adapter);
            Runnable update=()->{
                Integer selected=selection.selected();
                if(selected==null){
                    detail.setText("请选择存档位置");image.setImageDrawable(null);
                }else{
                    Record record=records.get(selected);
                    String label=slotLabel(selected);
                    detail.setText(record==null?label+" · 空白位置":
                        label+"\n"+record.time+"\n"+record.info);
                    Bitmap next=record!=null&&!record.thumbnail.isEmpty()?preview.read(record.thumbnail):null;
                    image.setImageBitmap(next);
                }
                if(dialog.getButton(AlertDialog.BUTTON_POSITIVE)!=null)
                    dialog.getButton(AlertDialog.BUTTON_POSITIVE).setEnabled(selection.canConfirm());
            };
            list.setOnItemClickListener((parent,view,position,id)->{
                if(!state.owns(request))return;
                if(selection.choose(visible.get(position)))request.draft=selection.confirm();
                update.run();
            });
            pages.setOnItemSelectedListener(new AdapterView.OnItemSelectedListener(){
                @Override public void onNothingSelected(AdapterView<?> parent){}
                @Override public void onItemSelected(AdapterView<?> parent,View view,int page,long id){
                    visible.clear();labels.clear();
                    if(loading){
                        for(Integer slot:ids){
                            boolean include=page==0||(page==11?slot>=10000:
                                slot>=page*100+1&&slot<=page*100+100);
                            if(include)visible.add(slot);
                        }
                    }else{
                        for(int slot=101+page*100;slot<=200+page*100;slot++)visible.add(slot);
                    }
                    java.util.Collections.sort(visible);
                    for(Integer slot:visible){
                        Record record=records.get(slot);
                        String description=record==null?"空白位置":record.info;
                        labels.add(slotLabel(slot)+"  "+description);
                    }
                    adapter.notifyDataSetChanged();
                    empty.setVisibility(visible.isEmpty()?View.VISIBLE:View.GONE);
                    list.clearChoices();
                    Integer selected=selection.selected();
                    if(selected!=null&&visible.contains(selected))list.setItemChecked(visible.indexOf(selected),true);
                    update.run();
                }
            });
            dialog.setOnShowListener(owner->{
                dialog.getButton(AlertDialog.BUTTON_POSITIVE).setOnClickListener(view->{
                    if(!state.owns(request)||!selection.canConfirm())return;
                    state.completeData(request,selection.confirm());dialog.dismiss();
                });
                update.run();
            });
            if(!loading){
                int current=data.getInt("current_slot");
                if(current>=101&&current<=1100)pages.setSelection((current-101)/100);
            }
            dialog.show();
            return dialog;
        }catch(Exception error){
            throw new IllegalArgumentException("Cannot show native slot picker",error);
        }
    }
    private static String slotLabel(int slot){
        if(slot==10001)return"自动存档";
        if(slot>=10003&&slot<=10010)return"快速存档 "+(slot-10002);
        return String.format(java.util.Locale.ROOT,"%03d",slot);
    }
}
