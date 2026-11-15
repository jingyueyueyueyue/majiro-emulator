package org.majiro.player;

import java.io.File;
import java.nio.file.Files;
import java.util.Arrays;
import java.util.concurrent.*;

public final class MediaCacheChecks {
    static int checks;
    static void check(boolean value, String message) {
        ++checks;
        if (!value) throw new AssertionError(message);
    }
    public static void main(String[] args) throws Exception {
        File root = new File(args[0]);
        byte[] bytes = new byte[65537];
        for (int i = 0; i < bytes.length; ++i) bytes[i] = (byte)i;
        File cached = MediaCache.materialize(root, "fixture.OGG", bytes);
        check(MediaCache.isValid(cached, bytes.length), "healthy native hit rejected");
        check(!MediaCache.isValid(cached, -1), "negative native size accepted");
        check(!MediaCache.isValid(new File(root, "unhashed.ogg"), bytes.length),
            "unhashed native path accepted");
        check(cached.getName().endsWith(".ogg"), "extension not preserved");
        check(Arrays.equals(bytes, Files.readAllBytes(cached.toPath())), "initial bytes");
        Files.setLastModifiedTime(cached.toPath(), java.nio.file.attribute.FileTime.fromMillis(123000));
        MediaCache.materialize(root, "fixture.ogg", bytes);
        check(cached.lastModified() == 123000, "healthy cache unnecessarily rewritten");
        Files.delete(cached.toPath());
        check(!MediaCache.isValid(cached, bytes.length), "evicted native hit accepted");
        MediaCache.materialize(root, "fixture.ogg", bytes);
        check(Arrays.equals(bytes, Files.readAllBytes(cached.toPath())), "deleted cache not rebuilt");
        Files.write(cached.toPath(), new byte[7]);
        check(!MediaCache.isValid(cached, bytes.length), "partial native hit accepted");
        MediaCache.materialize(root, "fixture.ogg", bytes);
        check(Arrays.equals(bytes, Files.readAllBytes(cached.toPath())), "partial cache not rebuilt");
        Files.write(cached.toPath(), new byte[bytes.length]);
        check(!MediaCache.isValid(cached, bytes.length), "same-size corrupt native hit accepted");
        MediaCache.materialize(root, "fixture.ogg", bytes);
        check(Arrays.equals(bytes, Files.readAllBytes(cached.toPath())), "same-size corruption not rebuilt");
        check(MediaCache.isValid(cached, bytes.length), "rebuilt native hit rejected");
        // Concurrent extraction must expose complete files and leave no partials.
        Files.delete(cached.toPath());
        ExecutorService pool = Executors.newFixedThreadPool(8);
        try {
            java.util.List<Future<File>> jobs = new java.util.ArrayList<>();
            for (int i = 0; i < 32; ++i)
                jobs.add(pool.submit(() -> MediaCache.materialize(root, "fixture.ogg", bytes)));
            for (Future<File> job : jobs)
                check(Arrays.equals(bytes, Files.readAllBytes(job.get().toPath())), "concurrent publication");
        } finally { pool.shutdownNow(); }
        File font = MediaCache.materialize(root, "private-fonts", "fixture.font", bytes);
        Files.write(font.toPath(), new byte[1]);
        MediaCache.materialize(root, "private-fonts", "fixture.font", bytes);
        check(Arrays.equals(bytes, Files.readAllBytes(font.toPath())), "font partial not rebuilt");
        File conflict = MediaCache.materialize(root, "blocked", "fixture.ogg", bytes);
        Files.delete(conflict.toPath());
        Files.createDirectory(conflict.toPath());
        Files.write(new File(conflict, "keep").toPath(), new byte[]{1});
        try {
            MediaCache.materialize(root, "blocked", "fixture.ogg", bytes);
            throw new AssertionError("invalid destination accepted");
        } catch (java.io.IOException expected) {
            check(new File(conflict, "keep").isFile(), "failed write damaged destination");
        }
        try (java.util.stream.Stream<java.nio.file.Path> paths = Files.walk(root.toPath())) {
            check(paths.noneMatch(p -> p.getFileName().toString().endsWith(".writing")), "temporary file leaked");
        }
        System.out.println("{\"status\":\"passed\",\"checks\":" + checks + "}");
    }
}
