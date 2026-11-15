package org.majiro.player;

import java.io.*;
import java.nio.file.*;
import java.security.MessageDigest;
import java.util.Arrays;

public final class StreamingMediaChecks {
    static int checks;
    static void check(boolean value, String why) {
        checks++;
        if (!value) throw new AssertionError(why);
    }
    static InputStream pattern(final long size) {
        return new InputStream() {
            long position;
            public int read() {
                return position == size ? -1 : (int)((position++ * 31 + 7) & 255);
            }
            public int read(byte[] buffer, int at, int count) {
                if (position == size) return -1;
                count = (int)Math.min(count, size - position);
                for (int i = 0; i < count; i++) buffer[at + i] = (byte)(position++ * 31 + 7);
                return count;
            }
        };
    }
    static String digest(InputStream input) throws Exception {
        MessageDigest hash = MessageDigest.getInstance("SHA-256");
        byte[] block = new byte[65536];
        int count;
        while ((count = input.read(block)) != -1) hash.update(block, 0, count);
        StringBuilder result = new StringBuilder();
        for (byte value : hash.digest()) result.append(String.format("%02x", value & 255));
        return result.toString();
    }
    static void reject(File root, InputStream stream, long expected) throws Exception {
        try {
            MediaCache.materialize(root, "failure", "fixture.wav", stream, expected);
            throw new AssertionError("broken input accepted");
        } catch (IOException correct) { checks++; }
        try (java.util.stream.Stream<Path> paths = Files.list(new File(root, "failure").toPath())) {
            check(!paths.findAny().isPresent(), "failed extraction leaked a file");
        }
    }
    public static void main(String[] args) throws Exception {
        File root = new File(args[0]); root.mkdirs();
        long size = 96L * 1024 * 1024 + 17;
        check(Runtime.getRuntime().maxMemory() < size, "test needs a heap smaller than the resource");
        String expected = digest(pattern(size));
        File loose = MediaCache.materialize(root, "loose", "large.WAV", pattern(size), -1);
        check(loose.length() == size && loose.getName().equals(expected + ".wav"), "large loose stream changed");
        check(MediaCache.isValid(loose, size), "large cache validation failed");
        // Real file prefix/suffix ensure an archive slice cannot leak neighbors.
        File archive = new File(root, "archive.bin");
        try (OutputStream output = new FileOutputStream(archive); InputStream input = pattern(size)) {
            output.write(new byte[19]);
            byte[] block = new byte[65536]; int count;
            while ((count = input.read(block)) != -1) output.write(block, 0, count);
            output.write(new byte[]{9,8,7,6});
        }
        try (RandomAccessFile source = new RandomAccessFile(archive, "r")) {
            final int[] maximum = {0};
            MediaSliceInputStream slice = new MediaSliceInputStream(19, size, (position, buffer, at, count) -> {
                maximum[0] = Math.max(maximum[0], count);
                source.seek(position);
                return source.read(buffer, at, Math.min(count, 49151));
            });
            File cached = MediaCache.materialize(root, "media", "large.wav", slice, size);
            check(cached.length() == size && cached.getName().equals(expected + ".wav"), "archive range changed");
            check(maximum[0] <= 65536 && source.getFilePointer() == 19 + size, "unbounded or neighbor read");
            check(slice.read(new byte[1], 0, 0) == 0 && slice.read() == -1, "slice EOF contract");
            try (RandomAccessFile damage = new RandomAccessFile(cached, "rw")) {
                damage.seek(size / 2); damage.write(0);
            }
            check(!MediaCache.isValid(cached, size), "same-size corruption accepted");
            source.seek(0);
            cached = MediaCache.materialize(root, "media", "large.wav",
                new MediaSliceInputStream(19, size, (position, buffer, at, count) -> {
                    source.seek(position); return source.read(buffer, at, count);
                }), size);
            check(MediaCache.isValid(cached, size), "large damaged cache not rebuilt");
            Files.delete(cached.toPath());
            cached = MediaCache.materialize(root, "media", "large.wav", pattern(size), size);
            check(MediaCache.isValid(cached, size), "large evicted cache not rebuilt");
        }
        reject(root, pattern(7), 8);
        reject(root, pattern(9), 8);
        reject(root, new InputStream() {
            public int read() throws IOException { throw new IOException("injected source failure"); }
        }, 8);
        reject(root, new MediaSliceInputStream(3, 8, (position, buffer, at, count) -> -1), 8);
        reject(root, new MediaSliceInputStream(3, 8, (position, buffer, at, count) -> 0), 8);
        try {
            new MediaSliceInputStream(Long.MAX_VALUE - 2, 4, (p,b,a,n) -> -1);
            throw new AssertionError("overflowing range accepted");
        } catch (IllegalArgumentException correct) { checks++; }
        try (java.util.stream.Stream<Path> paths = Files.walk(root.toPath())) {
            check(paths.noneMatch(p -> p.getFileName().toString().endsWith(".writing")), "temporary file leaked");
        }
        System.out.println("{\"status\":\"passed\",\"checks\":" + checks +
            ",\"resource_bytes\":" + size + ",\"heap_bytes\":" + Runtime.getRuntime().maxMemory() + "}");
    }
}
