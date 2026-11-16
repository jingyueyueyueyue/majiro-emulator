package org.majiro.player;

import java.io.File;
import java.io.ByteArrayInputStream;
import java.io.FileInputStream;
import java.io.FileOutputStream;
import java.io.IOException;
import java.io.InputStream;
import java.nio.file.Files;
import java.nio.file.StandardCopyOption;
import java.security.MessageDigest;
import java.util.HashMap;
import java.util.Locale;

/** Rebuildable, content-addressed cache files, published atomically. */
final class MediaCache {
    private static final class Validation {
        final long size;
        final long modified;
        Validation(long size, long modified) { this.size=size; this.modified=modified; }
    }
    private static final HashMap<String, Validation> verified = new HashMap<String, Validation>();
    private static String hex(byte[] bytes) {
        char[] result = new char[bytes.length * 2];
        char[] digits = "0123456789abcdef".toCharArray();
        for (int i = 0; i < bytes.length; ++i) {
            result[i * 2] = digits[(bytes[i] & 255) >>> 4];
            result[i * 2 + 1] = digits[bytes[i] & 15];
        }
        return new String(result);
    }

    private static boolean matches(File file, long expectedSize, byte[] digest) throws Exception {
        if (!file.isFile() || file.length() != expectedSize) return false;
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

    /** Validate the native cache hit without reading or extracting the archive. */
    static synchronized boolean isValid(File file, long expectedSize) throws Exception {
        String name = file.getName();
        if (expectedSize < 0 || !name.matches("[0-9a-f]{64}\\.[a-z0-9]{1,8}")) return false;
        String path=file.getAbsolutePath();
        long modified=file.lastModified();
        Validation cached=verified.get(path);
        if (cached!=null && cached.size==expectedSize && cached.modified==modified &&
            file.isFile() && file.length()==expectedSize) return true;
        byte[] digest = new byte[32];
        for (int i = 0; i < digest.length; ++i)
            digest[i] = (byte)((Character.digit(name.charAt(i * 2), 16) << 4)
                | Character.digit(name.charAt(i * 2 + 1), 16));
        boolean valid=matches(file, expectedSize, digest);
        if (valid) verified.put(path,new Validation(expectedSize,modified));
        else verified.remove(path);
        return valid;
    }

    static File materialize(File cacheRoot, String name, byte[] bytes) throws Exception {
        return materialize(cacheRoot, "media", name, bytes);
    }

    static synchronized File materialize(File cacheRoot, String category, String name, byte[] bytes) throws Exception {
        return materialize(cacheRoot, category, name, new ByteArrayInputStream(bytes), bytes.length);
    }

    /** Copy through a fixed buffer; media size is independent of the Java heap. */
    static synchronized File materialize(File cacheRoot, String category, String name,
                                        InputStream input, long expectedSize) throws Exception {
        if (expectedSize < -1) throw new IllegalArgumentException("invalid media size");
        File directory = new File(cacheRoot, category);
        if (!directory.isDirectory() && !directory.mkdirs() && !directory.isDirectory())
            throw new IOException("cannot create media cache: " + directory);
        String suffix = name.toLowerCase(Locale.ROOT);
        suffix = suffix.contains(".") ? suffix.substring(suffix.lastIndexOf('.')) : ".bin";
        if (!suffix.matches("\\.[a-z0-9]{1,8}")) suffix = ".bin";
        MessageDigest hash = MessageDigest.getInstance("SHA-256");
        File pending = Files.createTempFile(directory.toPath(), "media-", ".writing").toFile();
        try {
            long copied = 0;
            try (FileOutputStream output = new FileOutputStream(pending)) {
                byte[] buffer = new byte[65536];
                int count;
                while ((count = input.read(buffer)) != -1) {
                    if (count == 0) throw new IOException("media source made no read progress");
                    if (count > Long.MAX_VALUE - copied ||
                        (expectedSize >= 0 && count > expectedSize - copied))
                        throw new IOException("media source exceeds declared length");
                    output.write(buffer, 0, count);
                    hash.update(buffer, 0, count);
                    copied += count;
                }
                if (expectedSize >= 0 && copied != expectedSize)
                    throw new IOException("media source ended before declared length");
                output.getFD().sync();
            }
            byte[] digest = hash.digest();
            File destination = new File(directory, hex(digest) + suffix);
            if (matches(destination, copied, digest)) {
                verified.put(destination.getAbsolutePath(),new Validation(copied,destination.lastModified()));
                return destination;
            }
            Files.move(pending.toPath(), destination.toPath(),
                    StandardCopyOption.ATOMIC_MOVE, StandardCopyOption.REPLACE_EXISTING);
            verified.put(destination.getAbsolutePath(),new Validation(copied,destination.lastModified()));
            return destination;
        } finally {
            Files.deleteIfExists(pending.toPath());
        }
    }
}
