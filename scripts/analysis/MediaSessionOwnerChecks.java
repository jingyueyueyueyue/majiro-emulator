package org.majiro.player;

import java.io.IOException;
import java.util.concurrent.CountDownLatch;
import java.util.concurrent.TimeUnit;
import java.util.concurrent.atomic.AtomicReference;

public final class MediaSessionOwnerChecks {
    static int checks;
    static void check(boolean result,String why){++checks;if(!result)throw new AssertionError(why);}
    static final class Resource implements MediaSessionOwner.Resource {
        final CountDownLatch entered=new CountDownLatch(1),ready=new CountDownLatch(1),released=new CountDownLatch(1);
        volatile boolean closed,fail,releaseImmediately=true;
        int closes;
        Resource(boolean ready){if(ready)this.ready.countDown();}
        @Override public void open() throws Exception {
            entered.countDown();
            if(!ready.await(3,TimeUnit.SECONDS))throw new AssertionError("fixture hung");
            if(fail)throw new IOException("original decode error");
        }
        @Override public synchronized void close(){if(closed)return;closed=true;++closes;ready.countDown();if(releaseImmediately)released.countDown();}
        @Override public boolean awaitClosed(long ms)throws InterruptedException{return released.await(ms,TimeUnit.MILLISECONDS);}
        @Override public boolean isClosed(){return closed;}
    }
    static Thread start(MediaSessionOwner<Resource> owner,Resource resource,AtomicReference<Object> result){
        Thread thread=new Thread(()->{try{result.set(owner.play(resource));}catch(Exception error){result.set(error);}});
        thread.start();return thread;
    }
    public static void main(String[] arguments)throws Exception {
        for(int trial=0;trial<100;++trial) {
            MediaSessionOwner<Resource> owner=new MediaSessionOwner<Resource>();
            Resource slow=new Resource(false);AtomicReference<Object> result=new AtomicReference<Object>();
            Thread worker=start(owner,slow,result);
            check(slow.entered.await(1,TimeUnit.SECONDS),"startup entered");
            long begin=System.nanoTime();owner.stop();
            check(System.nanoTime()-begin<TimeUnit.MILLISECONDS.toNanos(200),"stop never waits on startup");
            worker.join(1500);check(!worker.isAlive()&&Boolean.FALSE.equals(result.get()),"canceled open ends");
            check(owner.current()==null&&slow.closes==1,"canceled owner detached once");
            Resource replacement=new Resource(true);
            check(owner.play(replacement)&&owner.current()==replacement,"reentry works");
            owner.stop();check(replacement.closes==1,"replacement released once");
        }
        MediaSessionOwner<Resource> owner=new MediaSessionOwner<Resource>();
        Resource old=new Resource(true);old.releaseImmediately=false;
        check(owner.play(old),"old resource started");
        Resource next=new Resource(true);AtomicReference<Object> result=new AtomicReference<Object>();
        Thread worker=start(owner,next,result);
        long until=System.nanoTime()+TimeUnit.SECONDS.toNanos(1);
        while(!old.closed&&System.nanoTime()<until)Thread.yield();
        check(old.closed,"replacement closes old resource");
        check(next.entered.getCount()==1,"new GL startup waits for old GL teardown");
        owner.stop();old.released.countDown();worker.join(1500);
        check(!worker.isAlive()&&Boolean.FALSE.equals(result.get())&&next.closed,"stop cancels pending replacement");
        Resource broken=new Resource(true);broken.fail=true;
        try{owner.play(broken);throw new AssertionError("failure lost");}catch(IOException expected){}
        check(broken.closed&&owner.current()==null,"failed startup retains no decoder");
        Resource good=new Resource(true);check(owner.play(good),"failure permits later reentry");owner.stop();
        check(owner.stopAndAwait(100),"already-completed cleanup can be awaited");
        Resource pending=new Resource(true);pending.releaseImmediately=false;
        check(owner.play(pending),"pending teardown fixture starts");
        check(!owner.stopAndAwait(1),"owner teardown timeout is explicit");
        pending.released.countDown();check(owner.stopAndAwait(100),"native shutdown can await GL completion");
        System.out.println("{\"status\":\"passed\",\"checks\":"+checks+"}");
    }
}
