package org.majiro.player;

import android.app.Activity;
import android.content.Context;

/* JADX INFO: loaded from: classes.dex */
public final class AppearanceSettings {
    public static final String DARK = "dark";
    public static final String LIGHT = "light";
    public static final String SYSTEM = "system";

    private AppearanceSettings() {
    }

    public static String get(Context context) {
        String string = context.getSharedPreferences("appearance", 0).getString("theme", "system");
        return ("dark".equals(string) || "light".equals(string)) ? string : "system";
    }

    public static void set(Context context, String str) {
        if (!"system".equals(str) && !"light".equals(str) && !"dark".equals(str)) {
            throw new IllegalArgumentException("未知主题");
        }
        if (!context.getSharedPreferences("appearance", 0).edit().putString("theme", str).commit()) {
            throw new IllegalStateException("设置保存失败");
        }
    }

    public static boolean isDark(Context context) {
        String str = get(context);
        if ("dark".equals(str)) {
            return true;
        }
        return !"light".equals(str) && (context.getResources().getConfiguration().uiMode & 48) == 32;
    }

    public static void applyTheme(Activity activity) {
        activity.setTheme(isDark(activity) ? 16974382 : 16974401);
    }

    public static String label(Context context) {
        String str = get(context);
        if ("dark".equals(str)) {
            return "深色";
        }
        return "light".equals(str) ? "浅色" : "跟随系统";
    }
}
