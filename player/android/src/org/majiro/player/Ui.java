package org.majiro.player;

import android.app.Activity;
import android.content.Context;
import android.content.res.ColorStateList;
import android.graphics.Typeface;
import android.graphics.drawable.GradientDrawable;
import android.graphics.drawable.RippleDrawable;
import android.os.Build;
import android.view.View;
import android.widget.TextView;

/* JADX INFO: loaded from: classes.dex */
final class Ui {

    static final class Colors {
        final int accent;
        final int accentSoft;
        final int alternate;
        final int background;
        final int ink;
        final int muted;
        final int onAccent;
        final int outline;
        final int ripple;
        final int surface;

        Colors(boolean z) {
            this.background = z ? -15722989 : -657421;
            this.surface = z ? -15130082 : -1;
            this.alternate = z ? -14537176 : -1182995;
            this.ink = z ? -1182481 : -15194850;
            this.muted = z ? -5786196 : -9930899;
            this.accent = z ? -7220555 : -14521516;
            this.onAccent = z ? -15715546 : -1;
            this.accentSoft = z ? -14270925 : -1839129;
            this.outline = z ? -13285827 : -2300450;
            this.ripple = z ? 848417461 : 606235476;
        }
    }

    private Ui() {
    }

    static int dp(Context context, float f) {
        return (int) ((f * context.getResources().getDisplayMetrics().density) + 0.5f);
    }

    static Colors colors(Context context) {
        return new Colors(AppearanceSettings.isDark(context));
    }

    static GradientDrawable shape(Context context, int i, float f) {
        GradientDrawable gradientDrawable = new GradientDrawable();
        gradientDrawable.setColor(i);
        gradientDrawable.setCornerRadius(dp(context, f));
        return gradientDrawable;
    }

    static GradientDrawable outlined(Context context, Colors colors, int i, float f) {
        GradientDrawable gradientDrawableShape = shape(context, i, f);
        gradientDrawableShape.setStroke(dp(context, 1.0f), colors.outline);
        return gradientDrawableShape;
    }

    static RippleDrawable ripple(Context context, Colors colors, int i, float f, boolean z) {
        return new RippleDrawable(ColorStateList.valueOf(colors.ripple), z ? outlined(context, colors, i, f) : shape(context, i, f), shape(context, -1, f));
    }

    static TextView text(Context context, String str, float f, int i, boolean z) {
        TextView textView = new TextView(context);
        textView.setText(str);
        textView.setTextSize(f);
        textView.setTextColor(i);
        textView.setFontFeatureSettings("kern");
        textView.setLineSpacing(0.0f, 1.08f);
        if (z) {
            textView.setTypeface(Typeface.create("sans-serif-medium", 0));
        }
        return textView;
    }

    static TextView button(Context context, String str, Colors colors, boolean z) {
        TextView textViewText = text(context, str, 14.0f, z ? colors.onAccent : colors.accent, true);
        textViewText.setGravity(17);
        textViewText.setMinHeight(dp(context, 48.0f));
        textViewText.setMinWidth(dp(context, 48.0f));
        textViewText.setPadding(dp(context, 18.0f), dp(context, 10.0f), dp(context, 18.0f), dp(context, 10.0f));
        textViewText.setBackground(ripple(context, colors, z ? colors.accent : colors.surface, 18.0f, !z));
        textViewText.setFocusable(true);
        return textViewText;
    }

    static void heading(View view) {
        if (Build.VERSION.SDK_INT >= 28) {
            view.setAccessibilityHeading(true);
        }
    }

    static void applyBars(Activity activity, Colors colors) {
        activity.getWindow().setStatusBarColor(colors.background);
        activity.getWindow().setNavigationBarColor(colors.background);
        activity.getWindow().getDecorView().setSystemUiVisibility(AppearanceSettings.isDark(activity) ? 0 : 8208);
    }
}
