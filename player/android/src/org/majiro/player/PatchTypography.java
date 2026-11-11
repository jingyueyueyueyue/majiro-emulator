package org.majiro.player;

import android.graphics.Typeface;

/** Data-only text patch styles, independent of game title or executable name. */
final class PatchTypography {
    private static final String PREFIX = "__majiro_style__";

    static String family(String requested) {
        if (!requested.startsWith(PREFIX)) return requested;
        int split = requested.indexOf(':', PREFIX.length());
        if (split < 0 || split + 1 == requested.length())
            throw new IllegalArgumentException("patch font family");
        return requested.substring(split + 1);
    }

    static Typeface apply(String requested, Typeface resolved) {
        if (!requested.startsWith(PREFIX)) return resolved;
        int split = requested.indexOf(':', PREFIX.length());
        int style = Integer.parseInt(requested.substring(PREFIX.length(), split));
        if (style < 0 || style > 3) throw new IllegalArgumentException("patch font style");
        return Typeface.create(resolved, style);
    }

    private PatchTypography() {}
}
