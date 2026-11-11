package org.majiro.player;

import android.content.Context;
import android.net.Uri;
import android.os.ParcelFileDescriptor;
import android.system.Os;
import android.system.ErrnoException;
import java.io.IOException;
import java.util.concurrent.CountDownLatch;
import java.util.concurrent.TimeUnit;
import tv.danmaku.ijk.media.player.IjkMediaPlayer;
import tv.danmaku.ijk.media.player.IMediaPlayer;
import tv.danmaku.ijk.media.player.misc.IMediaDataSource;

/** Software WMV/MPEG decoding over the same granted document descriptor. */
public final class IjkVideo {
    private static final class Source implements IMediaDataSource {
        private ParcelFileDescriptor descriptor;
        private final long size;
        Source(Context context, Uri uri) throws Exception {
            descriptor = context.getContentResolver().openFileDescriptor(uri, "r");
            if (descriptor == null) throw new IOException("movie descriptor unavailable");
            try{
                size = descriptor.getStatSize();
                if (size < 0) throw new IOException("movie provider does not expose a seekable file");
            }catch(Exception failure){
                try{descriptor.close();}catch(IOException cleanup){failure.addSuppressed(cleanup);}
                descriptor=null;throw failure;
            }
        }
        @Override public synchronized int readAt(long position, byte[] buffer, int offset, int count) throws IOException {
            if (descriptor == null) throw new IOException("movie source closed");
            if (position < 0 || offset < 0 || count < 0 || offset > buffer.length-count)
                throw new IOException("invalid movie read range");
            if(count==0)return 0;
            if (position >= size) return -1;
            try {
                return Os.pread(descriptor.getFileDescriptor(), buffer, offset,
                    (int)Math.min(count, size - position), position);
            } catch (ErrnoException error) { throw new IOException("movie random read failed", error); }
        }
        @Override public long getSize() { return size; }
        @Override public synchronized void close() throws IOException {
            if (descriptor != null) { descriptor.close(); descriptor = null; }
        }
    }

    public static IjkMediaPlayer open(Context context, Uri uri) throws Exception {
        IjkMediaPlayer player = new IjkMediaPlayer();
        Source source=null;
        try{
            player.setOption(IjkMediaPlayer.OPT_CATEGORY_PLAYER, "mediacodec", 0);
            player.setOption(IjkMediaPlayer.OPT_CATEGORY_PLAYER, "start-on-prepared", 0);
            player.setOption(IjkMediaPlayer.OPT_CATEGORY_FORMAT, "enable_accurate_seek", 1);
            source=new Source(context, uri);
            player.setDataSource(source);
            return player;
        }catch(Exception failure){
            try{player.release();}catch(RuntimeException cleanup){failure.addSuppressed(cleanup);}
            if(source!=null)try{source.close();}catch(IOException cleanup){failure.addSuppressed(cleanup);}
            throw failure;
        }
    }

    public static long duration(Context context, Uri uri) throws Exception {
        final IjkMediaPlayer player = open(context, uri);
        final CountDownLatch completed = new CountDownLatch(1);
        final long[] value = {0};
        final String[] error = {null};
        player.setOnPreparedListener(new IMediaPlayer.OnPreparedListener() {
            @Override public void onPrepared(IMediaPlayer owner) {
                value[0] = owner.getDuration(); completed.countDown();
            }
        });
        player.setOnErrorListener(new IMediaPlayer.OnErrorListener() {
            @Override public boolean onError(IMediaPlayer owner, int what, int extra) {
                error[0] = "IJK metadata error " + what + "/" + extra;
                completed.countDown(); return true;
            }
        });
        try {
            player.prepareAsync();
            if (!completed.await(30, TimeUnit.SECONDS)) throw new IOException("movie metadata timeout");
            if (error[0] != null) throw new IOException(error[0]);
            if (value[0] <= 0) throw new IOException("movie duration unavailable");
            return value[0];
        } finally { player.release(); }
    }
}
