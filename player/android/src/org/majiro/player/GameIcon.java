package org.majiro.player;

import android.content.ContentResolver;
import android.content.res.AssetFileDescriptor;
import android.graphics.Bitmap;
import android.graphics.BitmapFactory;
import android.net.Uri;

import java.io.ByteArrayOutputStream;
import java.io.FileInputStream;
import java.io.IOException;
import java.io.InputStream;
import java.nio.ByteBuffer;
import java.nio.channels.FileChannel;
import java.util.ArrayList;
import java.util.Collections;
import java.util.Comparator;
import java.util.List;

/**
 * Reads PE icon resources from a user-authorized SAF document. No executable is
 * loaded or started. Call on a worker thread; a missing/unsupported icon is null.
 */
public final class GameIcon {
    static final int MAX_SIDE = 512;
    static final int MAX_ICON_BYTES = 4 * 1024 * 1024;
    static final int MAX_READ_BYTES = 16 * 1024 * 1024;
    static final int MAX_STREAM_BYTES = 16 * 1024 * 1024;
    static final int MAX_ENTRIES = 8192;
    private static final int MAX_CANDIDATES = 256;
    private static final byte[] PNG = {(byte) 137, 80, 78, 71, 13, 10, 26, 10};

    private GameIcon() {}

    public static Bitmap load(ContentResolver resolver, Uri document) {
        if (resolver == null || document == null || !"content".equals(document.getScheme())) return null;
        try {
            try (AssetFileDescriptor descriptor = resolver.openAssetFileDescriptor(document, "r")) {
                if (descriptor == null) return null;
                try (FileInputStream stream = descriptor.createInputStream()) {
                    FileChannel channel = stream.getChannel();
                    long start = descriptor.getStartOffset();
                    long length = descriptor.getDeclaredLength();
                    long available = channel.size();
                    if (available == 0) throw new IOException("non-seekable provider");
                    if (length < 0) length = available - start;
                    if (start < 0 || length < 0 || start > available || length > available - start)
                        throw new InvalidIcon("invalid document extent");
                    return load(new ChannelSource(channel, start, length));
                }
            }
        } catch (InvalidIcon invalid) {
            return null;
        } catch (IOException | UnsupportedOperationException seekUnavailable) {
            // SAF providers may return a pipe. Never buffer an unbounded EXE.
            try (InputStream input = resolver.openInputStream(document)) {
                if (input == null) return null;
                ByteArrayOutputStream output = new ByteArrayOutputStream(65536);
                byte[] block = new byte[16384];
                int count;
                while ((count = input.read(block)) != -1) {
                    if (count == 0) {
                        int value = input.read();
                        if (value < 0) break;
                        if (output.size() == MAX_STREAM_BYTES) return null;
                        output.write(value);
                    } else {
                        if (count > MAX_STREAM_BYTES - output.size()) return null;
                        output.write(block, 0, count);
                    }
                }
                return load(new BytesSource(output.toByteArray()));
            } catch (IOException | RuntimeException | OutOfMemoryError unavailable) {
                return null;
            }
        } catch (RuntimeException | OutOfMemoryError unavailable) {
            return null;
        }
    }

    private static Bitmap load(Source source) throws IOException {
        Reader reader = new Reader(source);
        int attempts = 0;
        for (List<Candidate> group : reader.groups()) {
            for (Candidate candidate : group) {
                if (++attempts > 64) return null;
                try {
                    byte[] bytes = reader.image(candidate);
                    int[] dimensions = pngDimensions(bytes);
                    if (dimensions != null) {
                        if (dimensions[0] != candidate.width || dimensions[1] != candidate.height) continue;
                        BitmapFactory.Options bounds = new BitmapFactory.Options();
                        bounds.inJustDecodeBounds = true;
                        BitmapFactory.decodeByteArray(bytes, 0, bytes.length, bounds);
                        if (bounds.outWidth != dimensions[0] || bounds.outHeight != dimensions[1]) continue;
                        BitmapFactory.Options options = new BitmapFactory.Options();
                        options.inPreferredConfig = Bitmap.Config.ARGB_8888;
                        options.inScaled = false;
                        Bitmap bitmap = BitmapFactory.decodeByteArray(bytes, 0, bytes.length, options);
                        if (bitmap != null) {
                            if (bitmap.getWidth() == dimensions[0] && bitmap.getHeight() == dimensions[1])
                                return bitmap;
                            bitmap.recycle();
                        }
                    } else {
                        Pixels pixels = dib(bytes);
                        if (pixels.width == candidate.width && pixels.height == candidate.height)
                            return Bitmap.createBitmap(pixels.argb, pixels.width, pixels.height,
                                                       Bitmap.Config.ARGB_8888);
                    }
                } catch (InvalidIcon | IllegalArgumentException badImage) {
                    // Try a smaller image or the next group when one resource is broken.
                }
            }
        }
        return null;
    }

    interface Source {
        long size();
        byte[] read(long offset, int count) throws IOException;
    }

    static final class BytesSource implements Source {
        private final byte[] bytes;
        BytesSource(byte[] bytes) { this.bytes = bytes; }
        public long size() { return bytes.length; }
        public byte[] read(long offset, int count) throws IOException {
            extent(offset, count, bytes.length);
            byte[] result = new byte[count];
            System.arraycopy(bytes, (int) offset, result, 0, count);
            return result;
        }
    }

    private static final class ChannelSource implements Source {
        private final FileChannel channel;
        private final long start, length;
        ChannelSource(FileChannel channel, long start, long length) {
            this.channel = channel; this.start = start; this.length = length;
        }
        public long size() { return length; }
        public byte[] read(long offset, int count) throws IOException {
            extent(offset, count, length);
            byte[] result = new byte[count];
            ByteBuffer buffer = ByteBuffer.wrap(result);
            int stalls = 0;
            while (buffer.hasRemaining()) {
                int read = channel.read(buffer, start + offset + buffer.position());
                if (read < 0) throw new InvalidIcon("truncated document");
                if (read == 0 && ++stalls > 3) throw new IOException("provider made no progress");
                if (read > 0) stalls = 0;
            }
            return result;
        }
    }

    static final class InvalidIcon extends IOException {
        InvalidIcon(String message) { super(message); }
    }

    private static void extent(long offset, long count, long size) throws InvalidIcon {
        if (offset < 0 || count < 0 || offset > size || count > size - offset)
            throw new InvalidIcon("icon resource outside document");
    }

    private static int u16(byte[] b, int at) throws InvalidIcon {
        extent(at, 2, b.length);
        return (b[at] & 255) | ((b[at + 1] & 255) << 8);
    }

    private static long u32(byte[] b, int at) throws InvalidIcon {
        extent(at, 4, b.length);
        return (b[at] & 255L) | ((b[at + 1] & 255L) << 8) |
               ((b[at + 2] & 255L) << 16) | ((b[at + 3] & 255L) << 24);
    }

    private static final class Section {
        long address, size, offset;
    }

    private static final class Entry {
        long id, target;
        boolean directory;
        String name;
    }

    private static final Comparator<Entry> ENTRY_ORDER = new Comparator<Entry>() {
        @Override public int compare(Entry a, Entry b) {
            if (a.name == null && b.name == null) return Long.compare(a.id, b.id);
            if (a.name == null) return -1;
            if (b.name == null) return 1;
            return a.name.compareTo(b.name);
        }
    };

    private static final class Resource {
        Entry name, language;
        long offset;
        int size;
    }

    static final class Candidate {
        int width, height, bits, id, order;
        Resource resource;
    }

    /**
     * Fixed type/name/language traversal rather than recursive walking. Table,
     * candidate, byte and decode budgets also bound cyclic/hostile resources.
     */
    static final class Reader {
        private final Source source;
        private final List<Section> sections = new ArrayList<>();
        private long root, rootSize, headers;
        private int bytesRead, entriesRead, candidates;

        Reader(Source source) throws IOException {
            this.source = source;
            byte[] dos = read(0, 64);
            if (u16(dos, 0) != 0x5a4d) throw new InvalidIcon("not a DOS executable");
            long pe = u32(dos, 60);
            if (pe < 64 || pe > 4 * 1024 * 1024) throw new InvalidIcon("invalid PE header");
            byte[] coff = read(pe, 24);
            if (u32(coff, 0) != 0x4550) throw new InvalidIcon("not a PE executable");
            int count = u16(coff, 6), optionalSize = u16(coff, 20);
            if (count < 1 || count > 96 || optionalSize < 96 || optionalSize > 4096)
                throw new InvalidIcon("invalid PE tables");
            byte[] optional = read(pe + 24, optionalSize);
            int magic = u16(optional, 0), directory;
            if (magic == 0x10b) directory = 96;
            else if (magic == 0x20b) directory = 112;
            else throw new InvalidIcon("unsupported PE optional header");
            if (u32(optional, directory - 4) < 3) throw new InvalidIcon("PE has no resources");
            root = u32(optional, directory + 16);
            rootSize = u32(optional, directory + 20);
            if (root == 0 || rootSize < 16 || rootSize > 0x100000000L - root)
                throw new InvalidIcon("invalid PE resources");
            headers = u32(optional, 60);
            long sectionAt = pe + 24 + optionalSize;
            if (headers < sectionAt + count * 40L || headers > source.size())
                throw new InvalidIcon("invalid PE header extent");
            byte[] table = read(sectionAt, count * 40);
            for (int i = 0; i < count; i++) {
                Section section = new Section();
                section.address = u32(table, i * 40 + 12);
                section.size = u32(table, i * 40 + 16);
                section.offset = u32(table, i * 40 + 20);
                extent(section.offset, section.size, source.size());
                sections.add(section);
            }
            rva(root, 16);
        }

        private byte[] read(long offset, int count) throws IOException {
            extent(offset, count, source.size());
            if (count > MAX_READ_BYTES - bytesRead) throw new InvalidIcon("icon read budget exceeded");
            bytesRead += count;
            return source.read(offset, count);
        }

        private long rva(long address, int count) throws InvalidIcon {
            if (address < 0 || address > 0xffffffffL || count < 0 ||
                    count > 0x100000000L - address) throw new InvalidIcon("invalid resource RVA");
            long result = -1;
            if (address < headers && count <= headers - address) result = address;
            for (Section section : sections) {
                long relative = address - section.address;
                if (relative >= 0 && relative <= section.size && count <= section.size - relative) {
                    if (result != -1) throw new InvalidIcon("ambiguous resource RVA");
                    result = section.offset + relative;
                }
            }
            if (result == -1) throw new InvalidIcon("unmapped resource RVA");
            extent(result, count, source.size());
            return result;
        }

        private byte[] relative(long offset, int count) throws IOException {
            extent(offset, count, rootSize);
            return read(rva(root + offset, count), count);
        }

        private List<Entry> entries(long offset) throws IOException {
            byte[] header = relative(offset, 16);
            int count = u16(header, 12) + u16(header, 14);
            if (count > 2048 || count > MAX_ENTRIES - entriesRead)
                throw new InvalidIcon("resource table budget exceeded");
            entriesRead += count;
            byte[] table = relative(offset + 16, count * 8);
            List<Entry> result = new ArrayList<>();
            for (int i = 0; i < count; i++) {
                Entry entry = new Entry();
                long key = u32(table, i * 8), value = u32(table, i * 8 + 4);
                entry.id = key;
                if ((key & 0x80000000L) != 0) {
                    long nameAt = key & 0x7fffffffL;
                    int length = u16(relative(nameAt, 2), 0);
                    if (length > 512) throw new InvalidIcon("resource name too long");
                    byte[] name = relative(nameAt + 2, length * 2);
                    StringBuilder text = new StringBuilder(length);
                    for (int j = 0; j < length; j++) text.append((char) u16(name, j * 2));
                    entry.name = text.toString();
                }
                entry.target = value & 0x7fffffffL;
                entry.directory = (value & 0x80000000L) != 0;
                result.add(entry);
            }
            Collections.sort(result, ENTRY_ORDER);
            return result;
        }

        private List<Resource> resources(int type) throws IOException {
            List<Resource> result = new ArrayList<>();
            for (Entry kind : entries(0)) {
                if (kind.name != null || kind.id != type || !kind.directory) continue;
                for (Entry name : entries(kind.target)) {
                    if (!name.directory || name.target == 0 || name.target == kind.target) continue;
                    for (Entry language : entries(name.target)) {
                        if (language.directory) continue;
                        byte[] data = relative(language.target, 16);
                        long size = u32(data, 4);
                        if (size == 0 || size > MAX_ICON_BYTES) continue;
                        Resource resource = new Resource();
                        resource.name = name; resource.language = language;
                        resource.size = (int) size;
                        try {
                            resource.offset = rva(u32(data, 0), resource.size);
                            result.add(resource);
                        } catch (InvalidIcon badLeaf) {
                            // A broken icon leaf must not hide other usable images.
                        }
                    }
                }
            }
            return result;
        }

        List<List<Candidate>> groups() throws IOException {
            List<Resource> icons = resources(3);
            List<Resource> groups = resources(14);
            List<List<Candidate>> result = new ArrayList<>();
            // The first numeric/named group is the application's primary icon.
            // Within it prefer the largest image, then the highest color depth.
            for (Resource group : groups) {
                if (group.size > 6 + 256 * 14) continue;
                byte[] data = read(group.offset, group.size);
                try {
                    if (u16(data, 0) != 0 || u16(data, 2) != 1) continue;
                    int count = u16(data, 4);
                    if (count == 0 || count > 256 || 6 + count * 14 > data.length) continue;
                    List<Candidate> choices = new ArrayList<>();
                    for (int i = 0; i < count; i++) {
                        if (++candidates > MAX_CANDIDATES)
                            throw new InvalidIcon("icon candidate budget exceeded");
                        int at = 6 + i * 14;
                        int width = data[at] & 255, height = data[at + 1] & 255;
                        if (width == 0) width = 256;
                        if (height == 0) height = 256;
                        int id = u16(data, at + 12);
                        long size = u32(data, at + 8);
                        Resource best = null;
                        for (Resource icon : icons) {
                            if (icon.name.name != null || icon.name.id != id || icon.size != size) continue;
                            boolean same = ENTRY_ORDER.compare(icon.language, group.language) == 0;
                            if (best == null || (same && ENTRY_ORDER.compare(best.language, group.language) != 0))
                                best = icon;
                        }
                        if (best == null) continue;
                        Candidate candidate = new Candidate();
                        candidate.width = width; candidate.height = height;
                        candidate.bits = u16(data, at + 6);
                        candidate.id = id; candidate.order = i; candidate.resource = best;
                        choices.add(candidate);
                    }
                    Collections.sort(choices, new Comparator<Candidate>() {
                        @Override public int compare(Candidate a, Candidate b) {
                            int area = Integer.compare(b.width * b.height, a.width * a.height);
                            if (area != 0) return area;
                            int depth = Integer.compare(b.bits, a.bits);
                            return depth != 0 ? depth : Integer.compare(a.order, b.order);
                        }
                    });
                    if (!choices.isEmpty()) result.add(choices);
                } catch (InvalidIcon badGroup) {
                    if (candidates > MAX_CANDIDATES) throw badGroup;
                }
            }
            return result;
        }

        byte[] image(Candidate candidate) throws IOException {
            return read(candidate.resource.offset, candidate.resource.size);
        }
    }

    static int[] pngDimensions(byte[] data) throws InvalidIcon {
        if (data.length < PNG.length) return null;
        for (int i = 0; i < PNG.length; i++) if (data[i] != PNG[i]) return null;
        if (data.length < 33 || u32(data, 8) != 0x0d000000L ||
                data[12] != 'I' || data[13] != 'H' || data[14] != 'D' || data[15] != 'R')
            throw new InvalidIcon("invalid icon PNG header");
        long width = Integer.reverseBytes((int) u32(data, 16)) & 0xffffffffL;
        long height = Integer.reverseBytes((int) u32(data, 20)) & 0xffffffffL;
        dimensions(width, height);
        return new int[] {(int) width, (int) height};
    }

    private static void dimensions(long width, long height) throws InvalidIcon {
        if (width < 1 || height < 1 || width > MAX_SIDE || height > MAX_SIDE)
            throw new InvalidIcon("icon dimensions exceed limit");
    }

    static final class Pixels {
        final int width, height;
        final int[] argb;
        Pixels(int width, int height) {
            this.width = width; this.height = height; argb = new int[width * height];
        }
    }

    static Pixels dib(byte[] data) throws InvalidIcon {
        long header = u32(data, 0);
        boolean core = header == 12;
        if (!core && header != 40 && header != 52 && header != 56 && header != 108 && header != 124)
            throw new InvalidIcon("unsupported icon DIB header");
        extent(0, header, data.length);
        int width, doubledHeight, planes, bits;
        long compression = 0, colors = 0;
        if (core) {
            width = u16(data, 4); doubledHeight = u16(data, 6);
            planes = u16(data, 8); bits = u16(data, 10);
        } else {
            width = (int) u32(data, 4); doubledHeight = (int) u32(data, 8);
            planes = u16(data, 12); bits = u16(data, 14);
            compression = u32(data, 16); colors = u32(data, 32);
        }
        long absoluteHeight = Math.abs((long) doubledHeight);
        dimensions(width, absoluteHeight / 2);
        if ((absoluteHeight & 1) != 0 || planes != 1 ||
                (bits != 1 && bits != 4 && bits != 8 && bits != 16 && bits != 24 && bits != 32))
            throw new InvalidIcon("invalid icon DIB geometry");
        if (compression != 0 && compression != 3 && compression != 6)
            throw new InvalidIcon("compressed icon DIB is unsupported");
        if (compression != 0 && bits != 16 && bits != 32)
            throw new InvalidIcon("invalid icon bitfields");
        long red = bits == 16 ? 0x7c00L : 0xff0000L;
        long green = bits == 16 ? 0x03e0L : 0xff00L;
        long blue = bits == 16 ? 0x001fL : 0xffL;
        long alpha = bits == 32 ? 0xff000000L : 0;
        int position = (int) header;
        if (compression != 0) {
            int maskAt = 40;
            red = u32(data, maskAt); green = u32(data, maskAt + 4); blue = u32(data, maskAt + 8);
            alpha = (header >= 56 || compression == 6) ? u32(data, maskAt + 12) : 0;
            if (header == 40) position += compression == 6 ? 16 : 12;
            if (header == 52 && compression == 6) throw new InvalidIcon("missing DIB alpha field");
            long limit = bits == 32 ? 0xffffffffL : 0xffffL;
            if (!mask(red) || !mask(green) || !mask(blue) || (alpha != 0 && !mask(alpha)) ||
                    ((red | green | blue | alpha) & ~limit) != 0 ||
                    (red & green) != 0 || (red & blue) != 0 || (green & blue) != 0 ||
                    (alpha & (red | green | blue)) != 0 || (compression == 6 && alpha == 0))
                throw new InvalidIcon("invalid DIB channel masks");
        }
        if (bits <= 8) {
            if (colors == 0) colors = 1 << bits;
            if (colors > (1 << bits)) throw new InvalidIcon("invalid icon palette");
        } else if (colors > 256) throw new InvalidIcon("icon palette exceeds limit");
        int paletteStride = core ? 3 : 4;
        extent(position, colors * paletteStride, data.length);
        int[] palette = new int[(int) colors];
        for (int i = 0; i < palette.length; i++) {
            int at = position + i * paletteStride;
            palette[i] = 0xff000000 | ((data[at + 2] & 255) << 16) |
                         ((data[at + 1] & 255) << 8) | (data[at] & 255);
        }
        position += palette.length * paletteStride;
        int height = (int) (absoluteHeight / 2);
        int stride = ((width * bits + 31) / 32) * 4;
        int maskStride = ((width + 31) / 32) * 4;
        extent(position, (long) stride * height, data.length);
        int maskAt = position + stride * height;
        long maskLength = (long) maskStride * height;
        boolean hasMask = maskLength <= data.length - maskAt;
        if (!hasMask && bits != 32) throw new InvalidIcon("truncated icon AND mask");
        Pixels pixels = new Pixels(width, height);
        boolean anyAlpha = false;
        for (int y = 0; y < height; y++) {
            int sourceY = doubledHeight > 0 ? height - 1 - y : y;
            int row = position + sourceY * stride;
            for (int x = 0; x < width; x++) {
                int color;
                if (bits <= 8) {
                    int index = bits == 8 ? data[row + x] & 255 :
                        bits == 4 ? ((data[row + x / 2] & 255) >> ((1 - (x & 1)) * 4)) & 15 :
                                    ((data[row + x / 8] & 255) >> (7 - (x & 7))) & 1;
                    if (index >= palette.length) throw new InvalidIcon("icon palette index out of bounds");
                    color = palette[index];
                } else if (bits == 24) {
                    int at = row + x * 3;
                    color = 0xff000000 | ((data[at + 2] & 255) << 16) |
                            ((data[at + 1] & 255) << 8) | (data[at] & 255);
                } else {
                    long value = bits == 16 ? u16(data, row + x * 2) : u32(data, row + x * 4);
                    int opacity = alpha == 0 ? 255 : channel(value, alpha);
                    if (alpha != 0 && opacity != 0) anyAlpha = true;
                    color = (opacity << 24) | (channel(value, red) << 16) |
                            (channel(value, green) << 8) | channel(value, blue);
                }
                pixels.argb[y * width + x] = color;
            }
        }
        boolean legacyAlpha = bits == 32 && alpha != 0 && !anyAlpha;
        if (!hasMask && legacyAlpha) throw new InvalidIcon("icon has neither alpha nor AND mask");
        for (int y = 0; y < height; y++) {
            int sourceY = doubledHeight > 0 ? height - 1 - y : y;
            for (int x = 0; x < width; x++) {
                int at = y * width + x;
                if (legacyAlpha) pixels.argb[at] |= 0xff000000;
                // Real 32-bit alpha replaces the legacy mask. For older icons,
                // represent the AND mask as transparency against the card.
                if ((bits != 32 || alpha == 0 || legacyAlpha) && hasMask &&
                        ((data[maskAt + sourceY * maskStride + x / 8] & 255) & (0x80 >> (x & 7))) != 0)
                    pixels.argb[at] &= 0x00ffffff;
            }
        }
        return pixels;
    }

    private static boolean mask(long value) {
        if (value == 0) return false;
        long shifted = value >>> Long.numberOfTrailingZeros(value);
        return (shifted & (shifted + 1)) == 0;
    }

    private static int channel(long value, long mask) {
        int shift = Long.numberOfTrailingZeros(mask);
        long maximum = mask >>> shift;
        return (int) ((((value & mask) >>> shift) * 255 + maximum / 2) / maximum);
    }
}
