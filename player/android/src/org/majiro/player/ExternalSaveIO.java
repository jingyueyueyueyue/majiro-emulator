package org.majiro.player;

import android.app.Activity;
import android.content.Intent;
import android.net.Uri;
import java.io.ByteArrayOutputStream;
import java.io.InputStream;
import java.io.OutputStream;
import java.nio.charset.StandardCharsets;
import java.util.concurrent.CountDownLatch;

/** One SAF request at a time. The owner VM blocks; UI callbacks never touch VM. */
public final class ExternalSaveIO {
    private static final int REQUEST_FAMILY=0x8000;
    private static final int LIMIT=32*1024*1024;
    private static Request pending;
    private static int requestSequence;
    private static final class Request {
        final String action,data;
        int requestCode;
        final CountDownLatch completed=new CountDownLatch(1);
        volatile String result="CANCEL";
        volatile Exception failure;
        Request(String action,String data){this.action=action;this.data=data;}
    }
    private ExternalSaveIO(){}
    public static String exchange(final Activity activity,String action,String data) throws Exception {
        if(!action.equals("save")&&!action.equals("load"))throw new IllegalArgumentException("external save action");
        if(data==null||data.length()>LIMIT)throw new IllegalArgumentException("external save size bound");
        final Request request=new Request(action,data);
        synchronized(ExternalSaveIO.class){
            if(pending!=null)throw new IllegalStateException("external save picker already active");
            requestSequence=(requestSequence+1)&0x1fff;
            request.requestCode=REQUEST_FAMILY|requestSequence;
            pending=request;
        }
        activity.runOnUiThread(new Runnable(){@Override public void run(){
            synchronized(ExternalSaveIO.class){if(pending!=request)return;}
            try{
                Intent intent=new Intent(request.action.equals("save")?Intent.ACTION_CREATE_DOCUMENT:Intent.ACTION_OPEN_DOCUMENT);
                intent.addCategory(Intent.CATEGORY_OPENABLE);intent.setType("application/octet-stream");
                if(request.action.equals("save"))intent.putExtra(Intent.EXTRA_TITLE,"majiro-checkpoint.mjsav");
                activity.startActivityForResult(intent,request.requestCode);
            }catch(Exception failure){finish(request,"CANCEL",failure);}
        }});
        request.completed.await();
        if(request.failure!=null)throw request.failure;
        return request.result;
    }
    private static void finish(Request request,String result,Exception failure){
        synchronized(ExternalSaveIO.class){
            if(pending!=request)return;
            pending=null;request.result=result;request.failure=failure;request.completed.countDown();
        }
    }
    public static boolean result(final Activity activity,int code,int result,Intent data){
        if((code&0xe000)!=REQUEST_FAMILY)return false;
        final Request request;
        synchronized(ExternalSaveIO.class){request=pending;}
        if(request==null)return true;
        if(code!=request.requestCode)return true;
        if(result!=Activity.RESULT_OK||data==null||data.getData()==null){
            finish(request,"CANCEL",null);return true;
        }
        final Uri uri=data.getData();
        new Thread(new Runnable(){@Override public void run(){
            try{
                if(request.action.equals("save")){
                    byte[] bytes=request.data.getBytes(StandardCharsets.UTF_8);
                    if(bytes.length>LIMIT)throw new IllegalArgumentException("external save encoded size bound");
                    try(OutputStream stream=activity.getContentResolver().openOutputStream(uri,"wt")){
                        if(stream==null)throw new IllegalStateException("external save destination unavailable");
                        stream.write(bytes);stream.flush();
                    }
                    finish(request,"OK",null);
                }else{
                    ByteArrayOutputStream bytes=new ByteArrayOutputStream();
                    try(InputStream stream=activity.getContentResolver().openInputStream(uri)){
                        if(stream==null)throw new IllegalStateException("external save source unavailable");
                        byte[] buffer=new byte[65536];int count;
                        while((count=stream.read(buffer))!=-1){
                            if(bytes.size()>LIMIT-count)throw new IllegalArgumentException("external save size bound");
                            bytes.write(buffer,0,count);
                        }
                    }
                    byte[] raw=bytes.toByteArray();
                    for(byte value:raw)if(value<0)throw new IllegalArgumentException("external portable save must be ASCII");
                    finish(request,"OK\n"+new String(raw,StandardCharsets.US_ASCII),null);
                }
            }catch(Exception failure){finish(request,"CANCEL",failure);}
        }},"majiro-external-save").start();
        return true;
    }
    public static void cancel(){
        Request request;
        synchronized(ExternalSaveIO.class){request=pending;}
        if(request!=null)finish(request,"CANCEL",null);
    }
}
