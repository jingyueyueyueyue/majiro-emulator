"""Isolated Java tests for production SAF requests and IJK resource cleanup."""
from pathlib import Path
import json
import subprocess
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]/"convert"))
from port_common import ROOT, read_json, sha256, timestamp, write_json

STUBS = {
"android/net/Uri.java": """package android.net;public class Uri{}""",
"android/content/Intent.java": """package android.content;import android.net.Uri;
 public class Intent{
 public static final String ACTION_CREATE_DOCUMENT="save",ACTION_OPEN_DOCUMENT="load",
 CATEGORY_OPENABLE="open",EXTRA_TITLE="title";
 public Intent(String a){}public Intent addCategory(String c){return this;}
 public Intent setType(String t){return this;}public Intent putExtra(String k,String v){return this;}
 public Uri getData(){return new Uri();}
 }""",
"android/content/Context.java": """package android.content;
 public class Context{public ContentResolver resolver=new ContentResolver();
 public ContentResolver getContentResolver(){return resolver;}}""",
"android/content/ContentResolver.java": """package android.content;
 import java.io.*;import java.util.concurrent.*;import android.net.Uri;import android.os.ParcelFileDescriptor;
 public class ContentResolver{
 public volatile int writes;public volatile CountDownLatch entered=new CountDownLatch(1),release=new CountDownLatch(1);
 public ByteArrayOutputStream saved=new ByteArrayOutputStream();
 public volatile boolean blockingWrite;
 public volatile CountDownLatch writeEntered=new CountDownLatch(1),writeRelease=new CountDownLatch(1);
 public volatile int closes;
 public InputStream openInputStream(Uri u){return new ByteArrayInputStream("MJS1".getBytes());}
 public OutputStream openOutputStream(Uri u,String mode)throws Exception{
 writes++;entered.countDown();release.await();return new OutputStream(){
 public void write(int value)throws IOException{saved.write(value);}
 public void write(byte[] value)throws IOException{
 writeEntered.countDown();
 if(blockingWrite)try{writeRelease.await();}catch(InterruptedException e){throw new IOException(e);}
 saved.write(value);}
 public void close(){closes++;}
 };}
 public ParcelFileDescriptor openFileDescriptor(Uri u,String mode){return new ParcelFileDescriptor();}
 }""",
"android/app/Activity.java": """package android.app;import android.content.*;
 public class Activity extends Context{
 public static final int RESULT_OK=-1;public volatile int request;
 public void runOnUiThread(Runnable r){r.run();}
 public void startActivityForResult(Intent i,int code){request=code;}
 }""",
"android/os/ParcelFileDescriptor.java": """package android.os;import java.io.*;
 public class ParcelFileDescriptor{
 public static int opened,closed;public static long size=8;boolean dead;
 public ParcelFileDescriptor(){opened++;}
 public long getStatSize(){return size;}
 public FileDescriptor getFileDescriptor(){return new FileDescriptor();}
 public void close()throws IOException{if(dead)throw new IOException("double close");dead=true;closed++;}
 }""",
"android/system/ErrnoException.java": """package android.system;public class ErrnoException extends Exception{}""",
"android/system/Os.java": """package android.system;import java.io.*;
 public class Os{public static int pread(FileDescriptor fd,byte[] b,int o,int n,long p)throws ErrnoException{return n;}}""",
"tv/danmaku/ijk/media/player/misc/IMediaDataSource.java": """package tv.danmaku.ijk.media.player.misc;
 import java.io.*;public interface IMediaDataSource{
 int readAt(long p,byte[] b,int o,int n)throws IOException;long getSize();void close()throws IOException;}""",
"tv/danmaku/ijk/media/player/IMediaPlayer.java": """package tv.danmaku.ijk.media.player;
 public interface IMediaPlayer{
 long getDuration();interface OnPreparedListener{void onPrepared(IMediaPlayer p);}
 interface OnErrorListener{boolean onError(IMediaPlayer p,int w,int e);}
 }""",
"tv/danmaku/ijk/media/player/IjkMediaPlayer.java": """package tv.danmaku.ijk.media.player;
 import tv.danmaku.ijk.media.player.misc.IMediaDataSource;
 public class IjkMediaPlayer implements IMediaPlayer{
 public static final int OPT_CATEGORY_PLAYER=1,OPT_CATEGORY_FORMAT=2;
 public static int created,released;public static boolean fail;public static IMediaDataSource latest;
 boolean dead;IMediaDataSource source;OnPreparedListener prepared;
 public IjkMediaPlayer(){created++;}
 public void setOption(int c,String k,long v){}
 public void setDataSource(IMediaDataSource s){latest=s;if(fail)throw new IllegalStateException("injected");source=s;}
 public void release(){if(dead)throw new AssertionError("double player release");dead=true;released++;
 if(source!=null)try{source.close();}catch(Exception e){throw new RuntimeException(e);}}
 public void setOnPreparedListener(OnPreparedListener l){prepared=l;}
 public void setOnErrorListener(OnErrorListener l){}
 public long getDuration(){return 100;}
 public void prepareAsync(){prepared.onPrepared(this);}
 }""",
"org/majiro/player/DocumentMediaChecks.java": r"""package org.majiro.player;
import android.app.Activity;import android.content.Intent;import android.net.Uri;
import android.os.ParcelFileDescriptor;
import tv.danmaku.ijk.media.player.IjkMediaPlayer;
import java.util.concurrent.*;import java.util.concurrent.atomic.*;
public class DocumentMediaChecks{
 static int checks;
 static void check(boolean v,String why){checks++;if(!v)throw new AssertionError(why);}
 static Thread exchange(Activity a,AtomicReference<Object> result,String action){
 Thread t=new Thread(()->{try{result.set(ExternalSaveIO.exchange(a,action,"MJS1"));}
 catch(Exception e){result.set(e);}});t.start();return t;
 }
 static void ready(Activity a)throws Exception{
 long end=System.nanoTime()+TimeUnit.SECONDS.toNanos(2);
 while(a.request==0&&System.nanoTime()<end)Thread.sleep(1);
 check(a.request!=0,"picker not dispatched");
 }
 public static void main(String[]args)throws Exception{
 Activity a=new Activity();AtomicReference<Object> answer=new AtomicReference<>();
 Thread worker=exchange(a,answer,"save");ready(a);int code=a.request;
 check(ExternalSaveIO.result(a,code,Activity.RESULT_OK,new Intent("result")),"result not handled");
 check(a.resolver.entered.await(2,TimeUnit.SECONDS),"writer missing");
 ExternalSaveIO.result(a,code,Activity.RESULT_OK,new Intent("duplicate"));
 a.resolver.release.countDown();worker.join(2000);
 check(!worker.isAlive()&&"OK".equals(answer.get())&&a.resolver.writes==1,"duplicate result wrote twice");
 a.request=0;answer.set(null);worker=exchange(a,answer,"load");ready(a);
 worker.interrupt();worker.join(2000);
 check(answer.get()instanceof InterruptedException,"interrupted picker leaked");
 int obsolete=a.request;a.request=0;answer.set(null);worker=exchange(a,answer,"load");ready(a);
 ExternalSaveIO.result(a,obsolete,Activity.RESULT_OK,new Intent("stale"));
 check(worker.isAlive(),"stale result completed next request");
 ExternalSaveIO.result(a,a.request,Activity.RESULT_OK,new Intent("current"));worker.join(2000);
 check("OK\nMJS1".equals(answer.get()),"valid next request failed");
 a.request=0;answer.set(null);worker=exchange(a,answer,"load");ready(a);
 ExternalSaveIO.cancel();worker.join(2000);check("CANCEL".equals(answer.get()),"cancel failed");
 // Cancellation after result claiming must keep the slot until the provider
 // returns/closes; no old bytes may be written after CANCEL is delivered.
 Activity cancelled=new Activity();AtomicReference<Object> cancelledAnswer=new AtomicReference<>();
 Thread cancelling=exchange(cancelled,cancelledAnswer,"save");ready(cancelled);
 ExternalSaveIO.result(cancelled,cancelled.request,Activity.RESULT_OK,new Intent("claimed"));
 check(cancelled.resolver.entered.await(2,TimeUnit.SECONDS),"cancel fixture did not enter provider");
 ExternalSaveIO.cancel();
 check(cancelling.isAlive()&&cancelledAnswer.get()==null,"CANCEL delivered before writer quiesced");
 try{ExternalSaveIO.exchange(new Activity(),"load","");throw new AssertionError("cancelled writer lost slot");}
 catch(IllegalStateException expected){checks++;}
 cancelled.resolver.release.countDown();cancelling.join(2000);
 check("CANCEL".equals(cancelledAnswer.get())&&cancelled.resolver.saved.size()==0
   &&cancelled.resolver.closes==1,"cancelled writer wrote or retained stream");
 Activity publishing=new Activity();publishing.resolver.blockingWrite=true;
 publishing.resolver.release.countDown();AtomicReference<Object> publication=new AtomicReference<>();
 Thread published=exchange(publishing,publication,"save");ready(publishing);
 ExternalSaveIO.result(publishing,publishing.request,Activity.RESULT_OK,new Intent("write"));
 check(publishing.resolver.writeEntered.await(2,TimeUnit.SECONDS),"publication did not begin");
 ExternalSaveIO.cancel();
 check(published.isAlive()&&publication.get()==null,"ongoing publication reported false cancellation");
 publishing.resolver.writeRelease.countDown();published.join(2000);
 check("OK".equals(publication.get())&&publishing.resolver.saved.size()==4
   &&publishing.resolver.closes==1,"publication result did not reflect completed write");
 Activity after=new Activity();AtomicReference<Object> afterAnswer=new AtomicReference<>();
 Thread afterThread=exchange(after,afterAnswer,"load");ready(after);
 ExternalSaveIO.result(after,after.request,Activity.RESULT_OK,new Intent("after"));
 afterThread.join(2000);check("OK\nMJS1".equals(afterAnswer.get()),"next exchange blocked after cancellation");
 Activity interrupted=new Activity();AtomicReference<Object> interruptedAnswer=new AtomicReference<>();
 Thread interruptedThread=exchange(interrupted,interruptedAnswer,"save");ready(interrupted);
 ExternalSaveIO.result(interrupted,interrupted.request,Activity.RESULT_OK,new Intent("interrupted"));
 check(interrupted.resolver.entered.await(2,TimeUnit.SECONDS),"interrupt fixture did not enter provider");
 interruptedThread.interrupt();interruptedThread.join(2000);
 check(interruptedAnswer.get()instanceof InterruptedException,"claimed exchange did not propagate interrupt");
 try{ExternalSaveIO.exchange(new Activity(),"load","");throw new AssertionError("interrupted writer lost slot");}
 catch(IllegalStateException expected){checks++;}
 interrupted.resolver.release.countDown();
 long cleanupEnd=System.nanoTime()+TimeUnit.SECONDS.toNanos(2);
 while(interrupted.resolver.closes==0&&System.nanoTime()<cleanupEnd)Thread.sleep(1);
 check(interrupted.resolver.closes==1&&interrupted.resolver.saved.size()==0,"interrupted writer retained bytes or stream");
 ParcelFileDescriptor.size=-1;
 try{IjkVideo.open(a,new Uri());throw new AssertionError("unknown size accepted");}catch(java.io.IOException expected){}
 check(ParcelFileDescriptor.opened==ParcelFileDescriptor.closed,"nonseekable descriptor leaked");
 check(IjkMediaPlayer.created==IjkMediaPlayer.released,"nonseekable decoder leaked");
 ParcelFileDescriptor.size=8;IjkMediaPlayer.fail=true;
 try{IjkVideo.open(a,new Uri());throw new AssertionError("failed bind accepted");}catch(IllegalStateException expected){}
 check(ParcelFileDescriptor.opened==ParcelFileDescriptor.closed,"failed bind leaked source");
 check(IjkMediaPlayer.created==IjkMediaPlayer.released,"failed bind leaked decoder");
 IjkMediaPlayer.fail=false;IjkMediaPlayer p=IjkVideo.open(a,new Uri());
 check(IjkMediaPlayer.latest.readAt(8,new byte[1],0,0)==0,"zero read at EOF failed");
 check(IjkMediaPlayer.latest.readAt(8,new byte[1],0,1)==-1,"EOF failed");
 try{IjkMediaPlayer.latest.readAt(-1,new byte[1],0,1);throw new AssertionError("negative read accepted");}
 catch(java.io.IOException expected){checks++;}
 p.release();check(ParcelFileDescriptor.opened==ParcelFileDescriptor.closed,"normal release leaked source");
 System.out.println("{\"status\":\"passed\",\"checks\":"+checks+"}");
 }
}""",
}


def run():
    folder = ROOT.parent/"archive/2026-11/20261111_emulator_audit_host/document_media_io"
    sources = []
    for name, text in STUBS.items():
        file = folder/"src"/name
        file.parent.mkdir(parents=True, exist_ok=True)
        file.write_text(text, encoding="utf8")
        sources.append(file)
    production = [ROOT/"player/android/src/org/majiro/player"/name
                  for name in ("ExternalSaveIO.java", "IjkVideo.java")]
    java = Path(read_json(ROOT/"reports/android_toolchain.json")["java"])/"bin"
    for index, command in enumerate((
        [str(java/"javac.exe"), "-encoding", "UTF-8", "-d", str(folder/"classes"),
         *map(str, sources+production)],
        [str(java/"java.exe"), "-cp", str(folder/"classes"), "org.majiro.player.DocumentMediaChecks"],
    )):
        result = subprocess.run(command, capture_output=True, timeout=30)
        (folder/f"20261111_{index}.txt").write_bytes(result.stdout+result.stderr)
        assert result.returncode == 0, result.stderr.decode("utf8", errors="replace")
    report = json.loads(result.stdout)
    report.update(timestamp=timestamp(), device_accessed=False,
                  source_sha256={p.name: sha256(p) for p in production},
                  scope="real Java SAF/IJK adapter classes with deterministic Android/decoder stubs")
    write_json(folder/"20261111_result.json", report)
    print(json.dumps(report))


if __name__ == "__main__":
    run()
