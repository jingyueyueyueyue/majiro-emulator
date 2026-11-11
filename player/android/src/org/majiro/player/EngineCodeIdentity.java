package org.majiro.player;

import java.nio.charset.StandardCharsets;
import java.security.MessageDigest;
import java.security.NoSuchAlgorithmException;
import java.util.ArrayList;
import java.util.Collections;
import java.util.Comparator;

/** PE implementation identity; resources, timestamps and overlays are excluded. */
public final class EngineCodeIdentity {
    private static final int MAX_FILE = 128 * 1024 * 1024;
    private static final int MAX_SECTION = 64 * 1024 * 1024;
    private final byte[] source;

    private static final class Section {
        String name;
        long rva, flags;
        int length, offset, rawSize;
        byte[] image;
    }

    private EngineCodeIdentity(byte[] input) {
        require(input != null && input.length <= MAX_FILE, "unsupported PE input");
        source = input;
    }

    private static void require(boolean value, String reason) {
        if (!value) throw new IllegalArgumentException(reason);
    }

    private int checked(long at, int size) {
        require(at >= 0 && at <= (long)source.length - size, "truncated PE field");
        return (int)at;
    }

    private int word(long at) {
        int p = checked(at, 2);
        return (source[p] & 255) | (source[p + 1] & 255) << 8;
    }

    private long dword(long at) {
        int p = checked(at, 4);
        return (source[p] & 255L) | (source[p + 1] & 255L) << 8
            | (source[p + 2] & 255L) << 16 | (source[p + 3] & 255L) << 24;
    }

    private static void number(MessageDigest digest, long value, int bytes) {
        for (int i = 0; i < bytes; ++i) digest.update((byte)(value >>> (i * 8)));
    }

    private int storedOffset(ArrayList<Section> sections, long rva, long bytes) {
        for (Section section : sections) {
            long relative = rva - section.rva;
            if (relative >= 0 && relative + bytes <= section.rawSize)
                return checked(section.offset + relative, (int)bytes);
        }
        throw new IllegalArgumentException("PE relocation directory outside stored sections");
    }

    private String identity() {
        require(word(0) == 0x5a4d, "PE DOS signature missing");
        long pe = dword(0x3c);
        require(dword(pe) == 0x4550, "PE signature missing");
        int machine = word(pe + 4), count = word(pe + 6);
        require(machine == 0x14c && count > 0 && count <= 96, "unsupported PE machine/section count");
        long optional = pe + 24;
        int optionalSize = word(pe + 20);
        require(word(optional) == 0x10b && optionalSize >= 144, "unsupported PE optional header");
        long imageBase = dword(optional + 28);
        ArrayList<Section> sections = new ArrayList<Section>();
        ArrayList<Section> selected = new ArrayList<Section>();
        long total = 0;
        for (int i = 0; i < count; ++i) {
            long at = optional + optionalSize + i * 40L;
            int nameAt = checked(at, 8), end = nameAt + 8;
            while (end > nameAt && source[end - 1] == 0) --end;
            Section section = new Section();
            section.name = new String(source, nameAt, end - nameAt, StandardCharsets.US_ASCII);
            long virtual = dword(at + 8), rawSize = dword(at + 16), offset = dword(at + 20);
            long length = virtual == 0 ? rawSize : virtual;
            require(length <= MAX_SECTION && offset + rawSize <= source.length, "PE section outside input");
            section.rva = dword(at + 12);
            section.length = (int)length;
            section.offset = (int)offset;
            section.rawSize = (int)rawSize;
            section.flags = dword(at + 36);
            sections.add(section);
            if ((section.flags & 0x20000000L) != 0) selected.add(section);
        }
        if (selected.isEmpty())
            for (Section section : sections) if (section.name.equals(".text")) selected.add(section);
        require(!selected.isEmpty(), "PE has no identified implementation section");
        Collections.sort(selected, new Comparator<Section>() {
            @Override public int compare(Section left, Section right) {
                return Long.compare(left.rva, right.rva);
            }
        });
        for (Section section : selected) {
            total += section.length;
            require(total <= MAX_FILE, "PE implementation exceeds memory bound");
            section.image = new byte[section.length];
            System.arraycopy(source, section.offset, section.image, 0, Math.min(section.length, section.rawSize));
        }
        long relocRva = dword(optional + 136), relocBytes = dword(optional + 140);
        if (relocBytes != 0) {
            require(relocBytes <= MAX_FILE, "PE relocation directory exceeds bound");
            long cursor = storedOffset(sections, relocRva, relocBytes), finish = cursor + relocBytes;
            while (cursor < finish) {
                require(cursor + 8 <= finish, "truncated PE relocation block");
                long page = dword(cursor), block = dword(cursor + 4);
                require(block >= 8 && block % 2 == 0 && cursor + block <= finish, "invalid PE relocation block");
                for (long at = cursor + 8; at < cursor + block; at += 2) {
                    int entry = word(at), kind = entry >>> 12;
                    long target = page + (entry & 4095);
                    for (Section section : selected) {
                        long index = target - section.rva;
                        if (index < 0 || index >= section.length || kind == 0) continue;
                        require(kind == 3 && index + 4 <= section.length, "unsupported code relocation kind");
                        int p = (int)index;
                        byte[] image = section.image;
                        long value = (image[p] & 255L) | (image[p + 1] & 255L) << 8
                            | (image[p + 2] & 255L) << 16 | (image[p + 3] & 255L) << 24;
                        value = (value - imageBase) & 0xffffffffL;
                        for (int i = 0; i < 4; ++i) image[p + i] = (byte)(value >>> (i * 8));
                    }
                }
                cursor += block;
            }
        }
        try {
            MessageDigest digest = MessageDigest.getInstance("SHA-256");
            digest.update("MAJIRO-CODE-V1\0".getBytes(StandardCharsets.US_ASCII));
            number(digest, machine, 2);
            number(digest, selected.size(), 2);
            for (Section section : selected) {
                number(digest, section.rva, 4);
                number(digest, section.length, 4);
                digest.update(section.image);
            }
            StringBuilder result = new StringBuilder("code-sha256-v1:");
            for (byte value : digest.digest()) {
                result.append("0123456789abcdef".charAt((value >>> 4) & 15));
                result.append("0123456789abcdef".charAt(value & 15));
            }
            return result.toString();
        } catch (NoSuchAlgorithmException error) {
            throw new IllegalStateException("SHA-256 unavailable", error);
        }
    }

    public static String from(byte[] input) {
        return new EngineCodeIdentity(input).identity();
    }
}
