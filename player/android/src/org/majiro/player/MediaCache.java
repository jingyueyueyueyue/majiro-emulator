package org.majiro.player;

import java.io.File;
import java.io.FileInputStream;
import java.io.FileOutputStream;
import java.io.IOException;
import java.nio.file.Files;
import java.nio.file.StandardCopyOption;
import java.security.MessageDigest;
import java.util.Locale;

/** Rebuildable, content-addressed cache files, published atomically. */
final class MediaCache {
    private static String hex(byte[] bytes) {
        char[] result = new char[bytes.length * 2];
        char[] digits = "0123456789abcdef".toCharArray();
        for (int i = 0; i < bytes.length; ++i) {
            result[i * 2] = digits[(bytes[i] & 255) >>> 4];
            result[i * 2 + 1] = digits[bytes[i] & 15];
        }
        return new String(result);
    }

    private static boolean matches(File file, byte[] bytes, byte[] digest) throws Exception {
        if (!file.isFile() || file.length() != bytes.length) return false;
        MessageDigest hash = MessageDigest.getInstance("SHA-256");
        try (FileInputStream input = new FileInputStream(file)) {
            byte[] buffer = new byte[32768];
            int count;
            while ((count = input.read(buffer)) != -1) hash.update(buffer, 0, count);
        } catch (IOException missingOrEvicted) {
            return false;
        }
        return MessageDigest.isEqual(digest, hash.digest());
    }

    static File materialize(File cacheRoot, String name, byte[] bytes) throws Exception {
        return materialize(cacheRoot, "media", name, bytes);
    }

    static synchronized File materialize(File cacheRoot, String category, String name, byte[] bytes) throws Exception {
        File directory = new File(cacheRoot, category);
        if (!directory.isDirectory() && !directory.mkdirs() && !directory.isDirectory())
            throw new IOException("cannot create media cache: " + directory);
        String suffix = name.toLowerCase(Locale.ROOT);
        suffix = suffix.contains(".") ? suffix.substring(suffix.lastIndexOf('.')) : ".bin";
        if (!suffix.matches("\\.[a-z0-9]{1,8}")) suffix = ".bin";
        byte[] digest = MessageDigest.getInstance("SHA-256").digest(bytes);
        String hash = hex(digest);
        File destination = new File(directory, hash + suffix);
        if (matches(destination, bytes, digest)) return destination;
        File pending = Files.createTempFile(directory.toPath(), hash + "-", ".writing").toFile();
        try {
            try (FileOutputStream output = new FileOutputStream(pending)) {
                output.write(bytes);
                output.getFD().sync();
            }
            Files.move(pending.toPath(), destination.toPath(),
                    StandardCopyOption.ATOMIC_MOVE, StandardCopyOption.REPLACE_EXISTING);
        } finally {
            Files.deleteIfExists(pending.toPath());
        }
        return destination;
    }
}
