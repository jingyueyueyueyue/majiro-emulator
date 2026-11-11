package org.majiro.player;

import android.app.Activity;
import android.app.AlertDialog;
import android.content.DialogInterface;
import android.content.Intent;
import android.content.res.Configuration;
import android.net.Uri;
import android.os.Bundle;
import android.text.TextUtils;
import android.view.View;
import android.view.WindowInsets;
import android.widget.FrameLayout;
import android.widget.LinearLayout;
import android.widget.ScrollView;
import android.widget.TextView;
import android.widget.Toast;
import java.io.File;
import java.io.FileInputStream;
import java.io.OutputStream;
import java.util.concurrent.ExecutorService;
import java.util.concurrent.Executors;
import java.util.zip.ZipEntry;
import java.util.zip.ZipOutputStream;

/* JADX INFO: loaded from: classes.dex */
public final class SettingsActivity extends Activity {
    private Ui.Colors colors;
    private boolean destroyed;
    private LinearLayout page;
    private final ExecutorService worker = Executors.newSingleThreadExecutor();

    @Override // android.app.Activity
    public void onCreate(Bundle bundle) {
        AppearanceSettings.applyTheme(this);
        super.onCreate(bundle);
        DiagnosticLog.initialize(this);
        this.colors = Ui.colors(this);
        render();
    }

    /* JADX INFO: Access modifiers changed from: private */
    public void render() {
        String str;
        this.colors = Ui.colors(this);
        Ui.applyBars(this, this.colors);
        ScrollView scrollView = new ScrollView(this);
        scrollView.setFillViewport(true);
        scrollView.setBackgroundColor(this.colors.background);
        scrollView.setClipToPadding(false);
        this.page = new LinearLayout(this);
        this.page.setOrientation(1);
        this.page.setPadding(Ui.dp(this, 20.0f), Ui.dp(this, 12.0f), Ui.dp(this, 20.0f), Ui.dp(this, 32.0f));
        scrollView.addView(this.page, new FrameLayout.LayoutParams(-1, -2));
        scrollView.setOnApplyWindowInsetsListener(new View.OnApplyWindowInsetsListener() { // from class: org.majiro.player.SettingsActivity$$ExternalSyntheticLambda4
            @Override // android.view.View.OnApplyWindowInsetsListener
            public final WindowInsets onApplyWindowInsets(View view, WindowInsets windowInsets) {
                return SettingsActivity.this.m44lambda$render$0$orgmajiroplayerSettingsActivity(view, windowInsets);
            }
        });
        setContentView(scrollView);
        scrollView.requestApplyInsets();
        LinearLayout linearLayout = new LinearLayout(this);
        linearLayout.setGravity(16);
        TextView textViewText = Ui.text(this, "‹", 36.0f, this.colors.ink, false);
        textViewText.setGravity(17);
        textViewText.setContentDescription("返回");
        textViewText.setFocusable(true);
        textViewText.setBackground(Ui.ripple(this, this.colors, 0, 24.0f, false));
        textViewText.setOnClickListener(new View.OnClickListener() { // from class: org.majiro.player.SettingsActivity$$ExternalSyntheticLambda5
            @Override // android.view.View.OnClickListener
            public final void onClick(View view) {
                SettingsActivity.this.m45lambda$render$1$orgmajiroplayerSettingsActivity(view);
            }
        });
        linearLayout.addView(textViewText, new LinearLayout.LayoutParams(Ui.dp(this, 48.0f), Ui.dp(this, 48.0f)));
        TextView textViewText2 = Ui.text(this, "设置", 24.0f, this.colors.ink, true);
        LinearLayout.LayoutParams layoutParams = new LinearLayout.LayoutParams(0, -2, 1.0f);
        layoutParams.setMargins(Ui.dp(this, 12.0f), 0, 0, 0);
        linearLayout.addView(textViewText2, layoutParams);
        this.page.addView(linearLayout, new LinearLayout.LayoutParams(-1, Ui.dp(this, 56.0f)));
        section("通用");
        LinearLayout linearLayoutCard = card();
        row(linearLayoutCard, "界面主题", AppearanceSettings.label(this), new View.OnClickListener() { // from class: org.majiro.player.SettingsActivity$$ExternalSyntheticLambda6
            @Override // android.view.View.OnClickListener
            public final void onClick(View view) {
                SettingsActivity.this.m46lambda$render$2$orgmajiroplayerSettingsActivity(view);
            }
        }, true);
        row(linearLayoutCard, "文本与翻译", modeLabel(TranslationSettings.load(this).mode), new View.OnClickListener() { // from class: org.majiro.player.SettingsActivity$$ExternalSyntheticLambda7
            @Override // android.view.View.OnClickListener
            public final void onClick(View view) {
                SettingsActivity.this.m47lambda$render$3$orgmajiroplayerSettingsActivity(view);
            }
        }, false);
        this.page.addView(linearLayoutCard, new LinearLayout.LayoutParams(-1, -2));
        section("支持");
        LinearLayout linearLayoutCard2 = card();
        row(linearLayoutCard2, "诊断日志", "查看或导出", new View.OnClickListener() { // from class: org.majiro.player.SettingsActivity$$ExternalSyntheticLambda8
            @Override // android.view.View.OnClickListener
            public final void onClick(View view) {
                SettingsActivity.this.m48lambda$render$4$orgmajiroplayerSettingsActivity(view);
            }
        }, true);
        try {
            str = getPackageManager().getPackageInfo(getPackageName(), 0).versionName;
        } catch (Exception e) {
            str = "";
        }
        row(linearLayoutCard2, "关于 Majiro", str, new View.OnClickListener() { // from class: org.majiro.player.SettingsActivity$$ExternalSyntheticLambda9
            @Override // android.view.View.OnClickListener
            public final void onClick(View view) {
                SettingsActivity.this.m49lambda$render$5$orgmajiroplayerSettingsActivity(view);
            }
        }, false);
        this.page.addView(linearLayoutCard2, new LinearLayout.LayoutParams(-1, -2));
    }

    /* JADX INFO: renamed from: lambda$render$0$org-majiro-player-SettingsActivity, reason: not valid java name */
    /* synthetic */ WindowInsets m44lambda$render$0$orgmajiroplayerSettingsActivity(View view, WindowInsets windowInsets) {
        this.page.setPadding(Ui.dp(this, 20.0f) + windowInsets.getSystemWindowInsetLeft(), Ui.dp(this, 12.0f) + windowInsets.getSystemWindowInsetTop(), Ui.dp(this, 20.0f) + windowInsets.getSystemWindowInsetRight(), Ui.dp(this, 32.0f) + windowInsets.getSystemWindowInsetBottom());
        return windowInsets;
    }

    /* JADX INFO: renamed from: lambda$render$1$org-majiro-player-SettingsActivity, reason: not valid java name */
    /* synthetic */ void m45lambda$render$1$orgmajiroplayerSettingsActivity(View view) {
        finish();
    }

    /* JADX INFO: renamed from: lambda$render$2$org-majiro-player-SettingsActivity, reason: not valid java name */
    /* synthetic */ void m46lambda$render$2$orgmajiroplayerSettingsActivity(View view) {
        chooseTheme();
    }

    /* JADX INFO: renamed from: lambda$render$3$org-majiro-player-SettingsActivity, reason: not valid java name */
    /* synthetic */ void m47lambda$render$3$orgmajiroplayerSettingsActivity(View view) {
        TranslationSettingsDialog.show(this, this.worker, new Runnable() { // from class: org.majiro.player.SettingsActivity$$ExternalSyntheticLambda3
            @Override // java.lang.Runnable
            public final void run() {
                SettingsActivity.this.render();
            }
        });
    }

    /* JADX INFO: renamed from: lambda$render$4$org-majiro-player-SettingsActivity, reason: not valid java name */
    /* synthetic */ void m48lambda$render$4$orgmajiroplayerSettingsActivity(View view) {
        showLogs();
    }

    /* JADX INFO: renamed from: lambda$render$5$org-majiro-player-SettingsActivity, reason: not valid java name */
    /* synthetic */ void m49lambda$render$5$orgmajiroplayerSettingsActivity(View view) {
        about();
    }

    private void section(String str) {
        TextView textViewText = Ui.text(this, str, 13.0f, this.colors.muted, true);
        textViewText.setPadding(Ui.dp(this, 6.0f), 0, 0, 0);
        Ui.heading(textViewText);
        LinearLayout.LayoutParams layoutParams = new LinearLayout.LayoutParams(-1, -2);
        layoutParams.setMargins(0, Ui.dp(this, 24.0f), 0, Ui.dp(this, 10.0f));
        this.page.addView(textViewText, layoutParams);
    }

    private LinearLayout card() {
        LinearLayout linearLayout = new LinearLayout(this);
        linearLayout.setOrientation(1);
        linearLayout.setBackground(Ui.outlined(this, this.colors, this.colors.surface, 20.0f));
        return linearLayout;
    }

    private void row(LinearLayout linearLayout, String str, String str2, View.OnClickListener onClickListener, boolean z) {
        LinearLayout linearLayout2 = new LinearLayout(this);
        linearLayout2.setGravity(16);
        linearLayout2.setMinimumHeight(Ui.dp(this, 76.0f));
        linearLayout2.setPadding(Ui.dp(this, 18.0f), Ui.dp(this, 12.0f), Ui.dp(this, 14.0f), Ui.dp(this, 12.0f));
        linearLayout2.setBackground(Ui.ripple(this, this.colors, 0, 16.0f, false));
        linearLayout2.setFocusable(true);
        linearLayout2.setOnClickListener(onClickListener);
        LinearLayout linearLayout3 = new LinearLayout(this);
        linearLayout3.setOrientation(1);
        linearLayout3.addView(Ui.text(this, str, 16.0f, this.colors.ink, true));
        TextView textViewText = Ui.text(this, str2, 13.0f, this.colors.muted, false);
        textViewText.setMaxLines(2);
        textViewText.setEllipsize(TextUtils.TruncateAt.END);
        LinearLayout.LayoutParams layoutParams = new LinearLayout.LayoutParams(-1, -2);
        layoutParams.setMargins(0, Ui.dp(this, 5.0f), 0, 0);
        linearLayout3.addView(textViewText, layoutParams);
        linearLayout2.addView(linearLayout3, new LinearLayout.LayoutParams(0, -2, 1.0f));
        TextView textViewText2 = Ui.text(this, "›", 25.0f, this.colors.muted, false);
        textViewText2.setGravity(17);
        textViewText2.setImportantForAccessibility(2);
        linearLayout2.addView(textViewText2, new LinearLayout.LayoutParams(Ui.dp(this, 36.0f), Ui.dp(this, 48.0f)));
        linearLayout.addView(linearLayout2, new LinearLayout.LayoutParams(-1, -2));
        if (z) {
            View view = new View(this);
            view.setBackgroundColor(this.colors.outline);
            LinearLayout.LayoutParams layoutParams2 = new LinearLayout.LayoutParams(-1, Ui.dp(this, 1.0f));
            layoutParams2.setMargins(Ui.dp(this, 18.0f), 0, 0, 0);
            linearLayout.addView(view, layoutParams2);
        }
    }

    private static String modeLabel(String str) {
        if ("online".equals(str)) {
            return "在线翻译";
        }
        if ("local".equals(str)) {
            return "本地汉化";
        }
        return "original".equals(str) ? "日语原文" : "自动";
    }

    private void chooseTheme() {
        final String[] strArr = {"system", "light", "dark"};
        String str = AppearanceSettings.get(this);
        int i = 0;
        for (int i2 = 0; i2 < 3; i2++) {
            if (strArr[i2].equals(str)) {
                i = i2;
            }
        }
        new AlertDialog.Builder(this).setTitle("界面主题").setSingleChoiceItems(new String[]{"跟随系统", "浅色", "深色"}, i, new DialogInterface.OnClickListener() { // from class: org.majiro.player.SettingsActivity$$ExternalSyntheticLambda0
            @Override // android.content.DialogInterface.OnClickListener
            public final void onClick(DialogInterface dialogInterface, int i3) {
                SettingsActivity.this.m41lambda$chooseTheme$6$orgmajiroplayerSettingsActivity(strArr, dialogInterface, i3);
            }
        }).setNegativeButton("取消", (DialogInterface.OnClickListener) null).show();
    }

    /* JADX INFO: renamed from: lambda$chooseTheme$6$org-majiro-player-SettingsActivity, reason: not valid java name */
    /* synthetic */ void m41lambda$chooseTheme$6$orgmajiroplayerSettingsActivity(String[] strArr, DialogInterface dialogInterface, int i) {
        try {
            AppearanceSettings.set(this, strArr[i]);
            dialogInterface.dismiss();
            recreate();
        } catch (Exception e) {
            showError("未能保存", e.getMessage());
        }
    }

    private void showLogs() {
        File fileDirectory = DiagnosticLog.directory();
        File[] fileArrListFiles = fileDirectory == null ? null : fileDirectory.listFiles();
        new AlertDialog.Builder(this).setTitle("诊断日志").setMessage("日志保存在本机，用于排查启动和运行问题。\n\n当前 " + (fileArrListFiles == null ? 0 : fileArrListFiles.length) + " 份").setNegativeButton("关闭", (DialogInterface.OnClickListener) null).setPositiveButton("导出", new DialogInterface.OnClickListener() { // from class: org.majiro.player.SettingsActivity$$ExternalSyntheticLambda10
            @Override // android.content.DialogInterface.OnClickListener
            public final void onClick(DialogInterface dialogInterface, int i) {
                SettingsActivity.this.m51lambda$showLogs$7$orgmajiroplayerSettingsActivity(dialogInterface, i);
            }
        }).show();
    }

    /* JADX INFO: renamed from: lambda$showLogs$7$org-majiro-player-SettingsActivity, reason: not valid java name */
    /* synthetic */ void m51lambda$showLogs$7$orgmajiroplayerSettingsActivity(DialogInterface dialogInterface, int i) {
        startActivityForResult(new Intent("android.intent.action.CREATE_DOCUMENT").setType("application/zip").addCategory("android.intent.category.OPENABLE").putExtra("android.intent.extra.TITLE", "Majiro-logs.zip"), 1);
    }

    private void about() {
        String str;
        try {
            str = getPackageManager().getPackageInfo(getPackageName(), 0).versionName;
        } catch (Exception e) {
            str = "";
        }
        new AlertDialog.Builder(this).setTitle("Majiro 模拟器").setMessage("版本 " + str + "\n\n在 Android 上运行你自行添加的 Majiro 游戏。").setPositiveButton("关闭", (DialogInterface.OnClickListener) null).show();
    }

    @Override // android.app.Activity
    protected void onActivityResult(int i, int i2, Intent intent) {
        super.onActivityResult(i, i2, intent);
        if (i != 1 || i2 != -1 || intent == null || intent.getData() == null) {
            return;
        }
        exportLogs(intent.getData());
    }

    private void exportLogs(final Uri uri) {
        this.worker.execute(new Runnable() { // from class: org.majiro.player.SettingsActivity$$ExternalSyntheticLambda11
            @Override // java.lang.Runnable
            public final void run() {
                SettingsActivity.this.m43lambda$exportLogs$9$orgmajiroplayerSettingsActivity(uri);
            }
        });
    }

    /* JADX INFO: renamed from: lambda$exportLogs$9$org-majiro-player-SettingsActivity, reason: not valid java name */
    /* synthetic */ void m43lambda$exportLogs$9$orgmajiroplayerSettingsActivity(Uri uri) {
        try (OutputStream output = getContentResolver().openOutputStream(uri, "w");
             ZipOutputStream zip = new ZipOutputStream(output)) {
            File[] files = DiagnosticLog.directory().listFiles();
            if (files != null) {
                for (File file : files) {
                    if (!file.isFile() || !file.getName().endsWith(".log")) continue;
                    zip.putNextEntry(new ZipEntry(file.getName()));
                    try (FileInputStream input = new FileInputStream(file)) {
                        byte[] buffer = new byte[65536];
                        for (int count; (count = input.read(buffer)) != -1;) {
                            zip.write(buffer, 0, count);
                        }
                    }
                    zip.closeEntry();
                }
            }
            if (!this.destroyed) {
                runOnUiThread(() -> m42lambda$exportLogs$8$orgmajiroplayerSettingsActivity());
            }
        } catch (Exception error) {
            DiagnosticLog.failure("export", error);
            showError("\u5bfc\u51fa\u5931\u8d25", error.getMessage());
        }
    }

    /* JADX INFO: renamed from: lambda$exportLogs$8$org-majiro-player-SettingsActivity, reason: not valid java name */
    /* synthetic */ void m42lambda$exportLogs$8$orgmajiroplayerSettingsActivity() {
        Toast.makeText(this, "日志已导出", 0).show();
    }

    private void showError(final String str, final String str2) {
        if (this.destroyed) {
            return;
        }
        runOnUiThread(new Runnable() { // from class: org.majiro.player.SettingsActivity$$ExternalSyntheticLambda1
            @Override // java.lang.Runnable
            public final void run() {
                SettingsActivity.this.m50lambda$showError$10$orgmajiroplayerSettingsActivity(str, str2);
            }
        });
    }

    /* JADX INFO: renamed from: lambda$showError$10$org-majiro-player-SettingsActivity, reason: not valid java name */
    /* synthetic */ void m50lambda$showError$10$orgmajiroplayerSettingsActivity(String str, String str2) {
        if (this.destroyed) {
            return;
        }
        new AlertDialog.Builder(this).setTitle(str).setMessage(str2).setPositiveButton("知道了", (DialogInterface.OnClickListener) null).show();
    }

    @Override // android.app.Activity, android.content.ComponentCallbacks
    public void onConfigurationChanged(Configuration configuration) {
        super.onConfigurationChanged(configuration);
        render();
    }

    @Override // android.app.Activity
    public void onDestroy() {
        this.destroyed = true;
        this.worker.shutdownNow();
        super.onDestroy();
    }
}
