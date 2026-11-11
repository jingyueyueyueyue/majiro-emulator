package org.majiro.player;

import android.content.ContentResolver;
import android.graphics.Bitmap;
import android.net.Uri;
import java.io.File;
import java.nio.ByteBuffer;
import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.security.MessageDigest;
import java.util.List;
import java.util.Random;

/** Production parser/renderer with narrow Android IO/bitmap substitutes on JVM. */
public final class GameIconChecks {
    private static int checks, real, fuzz;
    private static void check(boolean value, String label) {
        ++checks;
        if (!value) throw new AssertionError(label + " (check " + checks + ")");
    }
    private static String digest(int[] pixels) throws Exception {
        MessageDigest sha = MessageDigest.getInstance("SHA-256");
        ByteBuffer values = ByteBuffer.allocate(pixels.length * 4);
        for (int color : pixels) values.putInt(color);
        StringBuilder result = new StringBuilder();
        for (byte value : sha.digest(values.array())) result.append(String.format("%02x", value & 255));
        return result.toString();
    }
    private static Bitmap load(File file, long start, long length, boolean stream) {
        return GameIcon.load(new ContentResolver(file, start, length, stream),
                             Uri.parse("content://authorized/game.exe"));
    }
    public static void main(String[] args) throws Exception {
        File folder = new File(args[0]);
        for (String row : Files.readAllLines(new File(folder, "cases.tsv").toPath(),
                                             StandardCharsets.UTF_8)) {
            String[] fields = row.split("\t");
            String kind = fields[0], label = fields[1];
            File file = new File(folder, fields[2]);
            if (kind.equals("dib")) {
                byte[] data = Files.readAllBytes(file.toPath());
                GameIcon.Pixels pixels = GameIcon.dib(data);
                check(pixels.width == Integer.parseInt(fields[3]) &&
                      pixels.height == Integer.parseInt(fields[4]), label + " dimensions");
                check(digest(pixels.argb).equals(fields[5]), label + " pixels");
            } else if (kind.equals("bad-dib")) {
                byte[] data = Files.readAllBytes(file.toPath());
                try {
                    GameIcon.dib(data);
                    throw new AssertionError(label + " accepted");
                } catch (GameIcon.InvalidIcon expected) { check(true, label); }
            } else if (kind.equals("png") || kind.equals("bad-png")) {
                byte[] data = Files.readAllBytes(file.toPath());
                try {
                    int[] dimensions = GameIcon.pngDimensions(data);
                    check(kind.equals("png") && dimensions != null &&
                          dimensions[0] == Integer.parseInt(fields[3]) &&
                          dimensions[1] == Integer.parseInt(fields[4]), label);
                } catch (GameIcon.InvalidIcon expected) { check(kind.equals("bad-png"), label); }
            } else if (kind.equals("pe") || kind.equals("bad-pe") || kind.equals("real")) {
                if (kind.equals("real")) file = new File(fields[2]);
                Bitmap bitmap = load(file, 0, -1, false);
                if (kind.equals("bad-pe")) check(bitmap == null, label);
                else {
                    check(bitmap != null, label + " loaded");
                    check(bitmap.getWidth() == Integer.parseInt(fields[3]) &&
                          bitmap.getHeight() == Integer.parseInt(fields[4]), label + " dimensions");
                    check(digest(bitmap.pixels).equals(fields[5]), label + " pixels");
                    if (kind.equals("real")) ++real;
                    else {
                        Bitmap streamed = load(file, 0, -1, true);
                        check(streamed != null && digest(streamed.pixels).equals(fields[5]),
                              label + " non-seekable SAF");
                    }
                }
            }
        }
        File good = new File(folder, "pe32.bin");
        byte[] data = Files.readAllBytes(good.toPath());
        File subrange = new File(folder, "prefixed.bin");
        byte[] prefixed = new byte[data.length + 37];
        System.arraycopy(data, 0, prefixed, 17, data.length);
        Files.write(subrange.toPath(), prefixed);
        Bitmap bitmap = load(subrange, 17, data.length, false);
        check(bitmap != null && bitmap.getWidth() == 2, "SAF subrange start/length honored");
        check(load(subrange, 17, 64, false) == null, "SAF subrange does not escape declared extent");
        ContentResolver resolver = new ContentResolver(good, 0, -1, false);
        check(GameIcon.load(resolver, Uri.parse("file:///arbitrary.exe")) == null &&
              resolver.opens == 0, "only user-authorized content URIs are opened");
        check(GameIcon.load(null, Uri.parse("content://x")) == null &&
              GameIcon.load(resolver, null) == null, "null input is a fallback");
        resolver.denied = true;
        check(GameIcon.load(resolver, Uri.parse("content://denied")) == null &&
              resolver.streamOpens == 0, "permission denial does not bypass SAF");
        resolver = new ContentResolver(good, 0, -1, true);
        resolver.generatedBytes = GameIcon.MAX_STREAM_BYTES + 4096;
        check(GameIcon.load(resolver, Uri.parse("content://pipe")) == null &&
              resolver.bytesRead <= GameIcon.MAX_STREAM_BYTES + 16384,
              "non-seekable input is bounded before allocating whole EXE");
        for (int length = 0; length < data.length; length++) {
            final byte[] truncated = java.util.Arrays.copyOf(data, length);
            try {
                GameIcon.Reader reader = new GameIcon.Reader(new GameIcon.BytesSource(truncated));
                for (List<GameIcon.Candidate> group : reader.groups())
                    for (GameIcon.Candidate candidate : group) reader.image(candidate);
            } catch (GameIcon.InvalidIcon expected) {}
            ++fuzz;
        }
        Random random = new Random(0x1c0ffeeL);
        for (int i = 0; i < 2000; i++) {
            byte[] mutated = data.clone();
            for (int j = 0; j < 1 + random.nextInt(5); j++) mutated[random.nextInt(mutated.length)] ^= (byte) (1 << random.nextInt(8));
            try {
                GameIcon.Reader reader = new GameIcon.Reader(new GameIcon.BytesSource(mutated));
                for (List<GameIcon.Candidate> group : reader.groups())
                    for (GameIcon.Candidate candidate : group) {
                        byte[] icon = reader.image(candidate);
                        if (GameIcon.pngDimensions(icon) == null) GameIcon.dib(icon);
                    }
            } catch (GameIcon.InvalidIcon expected) {}
            ++fuzz;
        }
        System.out.println("{\"status\":\"passed\",\"checks\":" + checks +
            ",\"real_executables\":" + real + ",\"truncation_and_mutation_cases\":" + fuzz + "}");
    }
}
