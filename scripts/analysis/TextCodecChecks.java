package org.majiro.player;

import java.nio.charset.CharacterCodingException;
import java.nio.charset.StandardCharsets;
import java.util.Arrays;
import java.util.concurrent.atomic.AtomicReference;

public final class TextCodecChecks {
    private static void check(boolean condition) {
        if (!condition) throw new AssertionError("codec result differs");
    }
    private static void exercise() throws Exception {
        byte[] japanese = "\u30eb\u30ea\u306e\u304b\u3055\u306d ABC 123 \uff76".getBytes(StandardCharsets.UTF_8);
        byte[] chinese = "\u7422\u7483\u4ea4\u9519 \u5b58\u6863".getBytes(StandardCharsets.UTF_8);
        for (int i = 0; i < 50000; ++i) {
            check(Arrays.equals(japanese, TextCodec.convert(
                TextCodec.convert(japanese, "UTF-8", "windows-31j"), "windows-31j", "UTF-8")));
            check(Arrays.equals(chinese, TextCodec.convert(
                TextCodec.convert(chinese, "UTF-8", "GBK"), "GBK", "UTF-8")));
            if (i % 500 == 0) {
                try {
                    TextCodec.convert(new byte[]{(byte)0x81}, "windows-31j", "UTF-8");
                    throw new AssertionError("truncated CP932 accepted");
                } catch (CharacterCodingException expected) {}
                try {
                    TextCodec.convert("\ud83d\ude00".getBytes(StandardCharsets.UTF_8), "UTF-8", "windows-31j");
                    throw new AssertionError("unmappable text accepted");
                } catch (CharacterCodingException expected) {}
                check(TextCodec.convert(new byte[0], "UTF-8", "windows-31j").length == 0);
            }
        }
    }
    public static void main(String[] args) throws Exception {
        AtomicReference<Throwable> failure = new AtomicReference<>();
        Thread[] threads = new Thread[4];
        for (int i = 0; i < threads.length; ++i) {
            threads[i] = new Thread(() -> {
                try { exercise(); } catch (Throwable error) { failure.set(error); }
            });
            threads[i].start();
        }
        for (Thread thread : threads) thread.join();
        if (failure.get() != null) throw new AssertionError(failure.get());
        System.out.println("{\"status\":\"passed\",\"conversions\":800000,\"threads\":4}");
    }
}
