package org.majiro.player;

import java.nio.ByteBuffer;
import java.nio.charset.Charset;
import java.nio.charset.CharsetDecoder;
import java.nio.charset.CharsetEncoder;
import java.nio.charset.CharacterCodingException;
import java.nio.charset.CodingErrorAction;
import java.util.LinkedHashMap;
import java.util.Map;

/** Strict, reusable converters. Android legacy codecs also own native ICU state. */
final class TextCodec {
    private static final int LIMIT = 8;
    private static final class Pair {
        final CharsetDecoder decoder;
        final CharsetEncoder encoder;
        Pair(Charset source, Charset target) {
            decoder = source.newDecoder().onMalformedInput(CodingErrorAction.REPORT)
                .onUnmappableCharacter(CodingErrorAction.REPORT);
            encoder = target.newEncoder().onMalformedInput(CodingErrorAction.REPORT)
                .onUnmappableCharacter(CodingErrorAction.REPORT);
        }
        byte[] convert(byte[] input) throws CharacterCodingException {
            try {
                ByteBuffer bytes = encoder.encode(decoder.decode(ByteBuffer.wrap(input)));
                byte[] result = new byte[bytes.remaining()];
                bytes.get(result);
                return result;
            } finally {
                decoder.reset();
                encoder.reset();
            }
        }
    }
    private static final ThreadLocal<Map<String, Pair>> CONVERTERS =
        ThreadLocal.withInitial(() -> new LinkedHashMap<String, Pair>(LIMIT, .75f, true) {
            @Override protected boolean removeEldestEntry(Map.Entry<String, Pair> entry) {
                return size() > LIMIT;
            }
        });

    static byte[] convert(byte[] input, String from, String to) throws CharacterCodingException {
        Charset source = Charset.forName(from), target = Charset.forName(to);
        String key = source.name() + "\n" + target.name();
        Map<String, Pair> pairs = CONVERTERS.get();
        Pair pair = pairs.get(key);
        if (pair == null) {
            pair = new Pair(source, target);
            pairs.put(key, pair);
        }
        return pair.convert(input);
    }
}
