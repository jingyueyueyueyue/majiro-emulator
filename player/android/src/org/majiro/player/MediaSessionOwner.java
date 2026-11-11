package org.majiro.player;

/** Serializes decoder replacement without holding UI-facing locks during startup/teardown. */
final class MediaSessionOwner<T extends MediaSessionOwner.Resource> {
    interface Resource {
        void open() throws Exception;
        void close();
        boolean awaitClosed(long timeoutMs) throws InterruptedException;
        boolean isClosed();
    }
    private final Object replacement=new Object();
    private T current,closing;
    private long generation;
    synchronized T current(){return current;}
    boolean play(T next) throws Exception {
        final long requested;
        synchronized(this){requested=generation;}
        synchronized(replacement) {
            T previous;
            synchronized(this) {
                if(requested!=generation){next.close();return false;}
                previous=current!=null?current:closing;
                current=null;if(previous!=null)closing=previous;
            }
            if(previous!=null) {
                previous.close();
                if(!previous.awaitClosed(5000)){
                    next.close();throw new IllegalStateException("previous movie cleanup timed out");
                }
            }
            synchronized(this) {
                if(requested!=generation){next.close();return false;}
                if(closing==previous)closing=null;
                current=next;
            }
            try {
                next.open();
                synchronized(this){return current==next&&!next.isClosed();}
            }catch(Exception error) {
                synchronized(this){if(current==next)current=null;closing=next;}
                next.close();throw error;
            }
        }
    }
    void stop() {
        final T previous;
        synchronized(this){++generation;previous=current;current=null;if(previous!=null)closing=previous;}
        if(previous!=null)previous.close();
    }
    boolean stopAndAwait(long timeoutMs) throws InterruptedException {
        stop();
        final T previous;
        synchronized(this){previous=closing;}
        return previous==null||previous.awaitClosed(timeoutMs);
    }
}
