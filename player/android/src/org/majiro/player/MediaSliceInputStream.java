package org.majiro.player;

import java.io.IOException;
import java.io.InputStream;

/** One seekable archive member. Never read into the following member. */
final class MediaSliceInputStream extends InputStream {
    interface Reader {
        int read(long position, byte[] buffer, int offset, int count) throws IOException;
    }
    private final Reader reader;
    private long position, remaining;
    MediaSliceInputStream(long offset, long size, Reader reader) {
        if (offset < 0 || size < 0 || offset > Long.MAX_VALUE - size)
            throw new IllegalArgumentException("invalid media archive range");
        this.position = offset;
        this.remaining = size;
        this.reader = reader;
    }
    @Override public int read() throws IOException {
        byte[] one = new byte[1];
        return read(one, 0, 1) < 0 ? -1 : one[0] & 255;
    }
    @Override public int read(byte[] buffer, int offset, int count) throws IOException {
        if (offset < 0 || count < 0 || offset > buffer.length - count)
            throw new IndexOutOfBoundsException();
        if (count == 0) return 0;
        if (remaining == 0) return -1;
        int requested = (int)Math.min(remaining, Math.min(count, 65536));
        int received = reader.read(position, buffer, offset, requested);
        if (received <= 0 || received > requested)
            throw new IOException("media archive range read failed at " + position);
        position += received;
        remaining -= received;
        return received;
    }
}
