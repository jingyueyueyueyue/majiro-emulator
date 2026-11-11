package org.majiro.player;

import android.content.Context;
import android.os.Build;
import java.io.File;
import java.io.FileOutputStream;
import java.io.PrintWriter;
import java.io.StringWriter;
import java.nio.charset.StandardCharsets;
import java.text.SimpleDateFormat;
import java.util.Arrays;
import java.util.Date;
import java.util.Locale;

/** Default local diagnostics; no uploads and no script transcript collection. */
public final class DiagnosticLog {
    private static File folder,current;
    private static boolean installed;
    private DiagnosticLog() {}
    private static void prune() {
        File[] files=folder.listFiles(file->file.isFile()&&file.getName().endsWith(".log"));
        if(files==null)return;
        Arrays.sort(files,(a,b)->a.getName().compareTo(b.getName()));
        for(int i=0;i<files.length-12;++i)if(!files[i].equals(current))files[i].delete();
    }
    public static synchronized void initialize(Context context) {
        if(folder!=null)return;
        File external=context.getExternalFilesDir(null);
        folder=new File(external==null?context.getFilesDir():external,"logs");
        if(!folder.isDirectory()&&!folder.mkdirs())throw new IllegalStateException("Cannot create diagnostic folder");
        current=new File(folder,new SimpleDateFormat("yyyyMMdd_HHmmss_SSS",Locale.ROOT).format(new Date())+"_session.log");
        if(!installed) {
            final Thread.UncaughtExceptionHandler previous=Thread.getDefaultUncaughtExceptionHandler();
            Thread.setDefaultUncaughtExceptionHandler((thread,error)->{
                failure("uncaught "+thread.getName(),error);if(previous!=null)previous.uncaughtException(thread,error);
            });installed=true;
        }
        write("device",Build.MANUFACTURER+" "+Build.MODEL+" Android "+Build.VERSION.RELEASE+" SDK "+Build.VERSION.SDK_INT);
        try {write("app",context.getPackageManager().getPackageInfo(context.getPackageName(),0).versionName);}
        catch(Exception ignored){}
        prune();
    }
    public static synchronized void write(String tag,String message) {
        android.util.Log.i("MajiroPlayer",tag+": "+message);
        if(current==null)return;
        try {
            if(current.length()>5*1024*1024) {
                current=new File(folder,new SimpleDateFormat("yyyyMMdd_HHmmss_SSS",Locale.ROOT).format(new Date())+"_session.log");
                current.createNewFile();prune();
            }
            String line=new SimpleDateFormat("yyyy-MM-dd HH:mm:ss.SSS",Locale.ROOT).format(new Date())+" "+tag+" "+message+"\n";
            try(FileOutputStream out=new FileOutputStream(current,true)){out.write(line.getBytes(StandardCharsets.UTF_8));}
        }catch(Exception ignored){android.util.Log.e("MajiroPlayer","local log write failed");}
    }
    public static void failure(String tag,Throwable error) {
        StringWriter text=new StringWriter();error.printStackTrace(new PrintWriter(text));write(tag,text.toString());
    }
    public static synchronized File directory(){return folder;}
}
