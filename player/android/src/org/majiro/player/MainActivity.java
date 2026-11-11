package org.majiro.player;

import android.app.Activity;
import android.app.AlertDialog;
import android.content.DialogInterface;
import android.content.Intent;
import android.content.UriPermission;
import android.content.res.Configuration;
import android.graphics.Bitmap;
import android.graphics.BitmapFactory;
import android.net.Uri;
import android.os.Bundle;
import android.text.TextUtils;
import android.view.View;
import android.view.WindowInsets;
import android.widget.EditText;
import android.widget.FrameLayout;
import android.widget.ImageView;
import android.widget.LinearLayout;
import android.widget.ProgressBar;
import android.widget.ScrollView;
import android.widget.TextView;
import android.widget.Toast;
import java.io.File;
import java.text.SimpleDateFormat;
import java.util.ArrayList;
import java.util.Calendar;
import java.util.Date;
import java.util.HashMap;
import java.util.Iterator;
import java.util.Locale;
import java.util.concurrent.ExecutorService;
import java.util.concurrent.Executors;

/* JADX INFO: loaded from: classes.dex */
public final class MainActivity extends Activity {
    private volatile boolean busy;
    private Ui.Colors colors;
    private volatile boolean destroyed;
    private GameLibrary library;
    private LinearLayout page;
    private boolean renderedDark;
    private final ExecutorService worker = Executors.newSingleThreadExecutor();
    private final HashMap<String, Bitmap> artworkCache = new HashMap<>();

    @Override // android.app.Activity
    public void onCreate(Bundle bundle) {
        String stringExtra;
        AppearanceSettings.applyTheme(this);
        super.onCreate(bundle);
        DiagnosticLog.initialize(this);
        this.library = new GameLibrary(this);
        this.colors = Ui.colors(this);
        this.renderedDark = AppearanceSettings.isDark(this);
        render();
        if (bundle == null && (getApplicationInfo().flags & 2) != 0 && (stringExtra = getIntent().getStringExtra("diagnostic_root")) != null) {
            getIntent().removeExtra("diagnostic_root");
            String stringExtra2 = getIntent().getStringExtra("diagnostic_mode");
            for (GameLibrary.Entry entry : this.library.all()) {
                if (stringExtra.equals(entry.root)) {
                    launch(entry, stringExtra2);
                    return;
                }
            }
            DiagnosticLog.write("diagnostic", "requested root is not in the authorized library");
        }
        if (this.library.all().isEmpty()) {
            final ArrayList arrayList = new ArrayList();
            for (UriPermission uriPermission : getContentResolver().getPersistedUriPermissions()) {
                if (uriPermission.isReadPermission()) {
                    arrayList.add(uriPermission.getUri());
                }
            }
            if (!arrayList.isEmpty()) {
                this.worker.execute(new Runnable() { // from class: org.majiro.player.MainActivity$$ExternalSyntheticLambda12
                    @Override // java.lang.Runnable
                    public final void run() {
                        MainActivity.this.m33lambda$onCreate$0$orgmajiroplayerMainActivity(arrayList);
                    }
                });
            }
        }
    }

    /* JADX INFO: renamed from: lambda$onCreate$0$org-majiro-player-MainActivity, reason: not valid java name */
    /* synthetic */ void m33lambda$onCreate$0$orgmajiroplayerMainActivity(ArrayList arrayList) {
        Iterator it = arrayList.iterator();
        while (it.hasNext()) {
            try {
                this.library.addTree((Uri) it.next());
            } catch (Exception e) {
                DiagnosticLog.write("library", "retained directory: " + e);
            }
        }
        refresh();
    }

    private void refresh() {
        if (!this.destroyed) {
            runOnUiThread(new Runnable() { // from class: org.majiro.player.MainActivity$$ExternalSyntheticLambda14
                @Override // java.lang.Runnable
                public final void run() {
                    MainActivity.this.m34lambda$refresh$1$orgmajiroplayerMainActivity();
                }
            });
        }
    }

    /* JADX INFO: renamed from: lambda$refresh$1$org-majiro-player-MainActivity, reason: not valid java name */
    /* synthetic */ void m34lambda$refresh$1$orgmajiroplayerMainActivity() {
        if (this.destroyed) {
            return;
        }
        render();
    }

    private void render() {
        String str;
        this.colors = Ui.colors(this);
        Ui.applyBars(this, this.colors);
        ScrollView scrollView = new ScrollView(this);
        scrollView.setFillViewport(true);
        scrollView.setBackgroundColor(this.colors.background);
        scrollView.setClipToPadding(false);
        this.page = new LinearLayout(this);
        this.page.setOrientation(1);
        this.page.setPadding(Ui.dp(this, 24.0f), Ui.dp(this, 16.0f), Ui.dp(this, 24.0f), Ui.dp(this, 32.0f));
        scrollView.addView(this.page, new FrameLayout.LayoutParams(-1, -2));
        scrollView.setOnApplyWindowInsetsListener(new View.OnApplyWindowInsetsListener() { // from class: org.majiro.player.MainActivity$$ExternalSyntheticLambda6
            @Override // android.view.View.OnApplyWindowInsetsListener
            public final WindowInsets onApplyWindowInsets(View view, WindowInsets windowInsets) {
                return MainActivity.this.m35lambda$render$2$orgmajiroplayerMainActivity(view, windowInsets);
            }
        });
        setContentView(scrollView);
        scrollView.requestApplyInsets();
        LinearLayout linearLayout = new LinearLayout(this);
        linearLayout.setGravity(16);
        ImageView imageView = new ImageView(this);
        imageView.setImageDrawable(new DefaultGameIcon());
        imageView.setImportantForAccessibility(2);
        linearLayout.addView(imageView, new LinearLayout.LayoutParams(Ui.dp(this, 40.0f), Ui.dp(this, 40.0f)));
        TextView textViewText = Ui.text(this, "Majiro", 20.0f, this.colors.ink, true);
        LinearLayout.LayoutParams layoutParams = new LinearLayout.LayoutParams(0, -2, 1.0f);
        layoutParams.setMargins(Ui.dp(this, 12.0f), 0, 0, 0);
        linearLayout.addView(textViewText, layoutParams);
        TextView textViewButton = Ui.button(this, "设置", this.colors, false);
        textViewButton.setOnClickListener(new View.OnClickListener() { // from class: org.majiro.player.MainActivity$$ExternalSyntheticLambda7
            @Override // android.view.View.OnClickListener
            public final void onClick(View view) {
                MainActivity.this.m36lambda$render$3$orgmajiroplayerMainActivity(view);
            }
        });
        linearLayout.addView(textViewButton);
        this.page.addView(linearLayout, new LinearLayout.LayoutParams(-1, Ui.dp(this, 52.0f)));
        ArrayList<GameLibrary.Entry> arrayListAll = this.library.all();
        LinearLayout linearLayout2 = new LinearLayout(this);
        linearLayout2.setGravity(80);
        LinearLayout linearLayout3 = new LinearLayout(this);
        linearLayout3.setOrientation(1);
        TextView textViewText2 = Ui.text(this, "游戏库", 32.0f, this.colors.ink, true);
        Ui.heading(textViewText2);
        linearLayout3.addView(textViewText2);
        if (this.busy) {
            str = "正在扫描文件夹…";
        } else {
            str = arrayListAll.isEmpty() ? "从本地文件夹添加游戏" : arrayListAll.size() + " 个游戏";
        }
        TextView textViewText3 = Ui.text(this, str, 14.0f, this.colors.muted, false);
        textViewText3.setPadding(0, Ui.dp(this, 8.0f), 0, 0);
        linearLayout3.addView(textViewText3);
        linearLayout2.addView(linearLayout3, new LinearLayout.LayoutParams(0, -2, 1.0f));
        TextView textViewButton2 = Ui.button(this, "＋  添加游戏", this.colors, true);
        textViewButton2.setEnabled(true ^ this.busy);
        textViewButton2.setAlpha(this.busy ? 0.5f : 1.0f);
        textViewButton2.setOnClickListener(new View.OnClickListener() { // from class: org.majiro.player.MainActivity$$ExternalSyntheticLambda8
            @Override // android.view.View.OnClickListener
            public final void onClick(View view) {
                MainActivity.this.m37lambda$render$4$orgmajiroplayerMainActivity(view);
            }
        });
        linearLayout2.addView(textViewButton2);
        LinearLayout.LayoutParams layoutParams2 = new LinearLayout.LayoutParams(-1, -2);
        layoutParams2.setMargins(0, Ui.dp(this, 28.0f), 0, Ui.dp(this, 20.0f));
        this.page.addView(linearLayout2, layoutParams2);
        if (arrayListAll.isEmpty()) {
            renderEmpty();
        } else {
            renderGames(arrayListAll);
        }
    }

    /* JADX INFO: renamed from: lambda$render$2$org-majiro-player-MainActivity, reason: not valid java name */
    /* synthetic */ WindowInsets m35lambda$render$2$orgmajiroplayerMainActivity(View view, WindowInsets windowInsets) {
        this.page.setPadding(Ui.dp(this, 24.0f) + windowInsets.getSystemWindowInsetLeft(), Ui.dp(this, 16.0f) + windowInsets.getSystemWindowInsetTop(), Ui.dp(this, 24.0f) + windowInsets.getSystemWindowInsetRight(), Ui.dp(this, 32.0f) + windowInsets.getSystemWindowInsetBottom());
        return windowInsets;
    }

    /* JADX INFO: renamed from: lambda$render$3$org-majiro-player-MainActivity, reason: not valid java name */
    /* synthetic */ void m36lambda$render$3$orgmajiroplayerMainActivity(View view) {
        startActivity(new Intent(this, (Class<?>) SettingsActivity.class));
    }

    /* JADX INFO: renamed from: lambda$render$4$org-majiro-player-MainActivity, reason: not valid java name */
    /* synthetic */ void m37lambda$render$4$orgmajiroplayerMainActivity(View view) {
        if (this.busy) {
            return;
        }
        choose();
    }

    private void renderEmpty() {
        LinearLayout linearLayout = new LinearLayout(this);
        linearLayout.setOrientation(1);
        linearLayout.setGravity(1);
        linearLayout.setPadding(Ui.dp(this, 28.0f), Ui.dp(this, 36.0f), Ui.dp(this, 28.0f), Ui.dp(this, 32.0f));
        linearLayout.setBackground(Ui.outlined(this, this.colors, this.colors.surface, 24.0f));
        if (this.busy) {
            ProgressBar progressBar = new ProgressBar(this);
            progressBar.setIndeterminate(true);
            linearLayout.addView(progressBar, new LinearLayout.LayoutParams(Ui.dp(this, 44.0f), Ui.dp(this, 44.0f)));
        } else {
            ImageView imageView = new ImageView(this);
            imageView.setImageDrawable(new DefaultGameIcon());
            imageView.setImportantForAccessibility(2);
            linearLayout.addView(imageView, new LinearLayout.LayoutParams(Ui.dp(this, 68.0f), Ui.dp(this, 68.0f)));
        }
        TextView textViewText = Ui.text(this, this.busy ? "正在添加游戏" : "添加你的第一个游戏", 22.0f, this.colors.ink, true);
        textViewText.setGravity(17);
        textViewText.setPadding(0, Ui.dp(this, 20.0f), 0, 0);
        linearLayout.addView(textViewText);
        TextView textViewText2 = Ui.text(this, this.busy ? "正在读取目录和游戏图标，请稍候" : "选择一个游戏目录，或包含多个游戏的上级目录", 14.0f, this.colors.muted, false);
        textViewText2.setGravity(17);
        textViewText2.setMaxWidth(Ui.dp(this, 420.0f));
        textViewText2.setPadding(0, Ui.dp(this, 10.0f), 0, 0);
        linearLayout.addView(textViewText2);
        if (!this.busy) {
            TextView textViewButton = Ui.button(this, "选择文件夹", this.colors, true);
            textViewButton.setOnClickListener(new View.OnClickListener() { // from class: org.majiro.player.MainActivity$$ExternalSyntheticLambda2
                @Override // android.view.View.OnClickListener
                public final void onClick(View view) {
                    MainActivity.this.m38lambda$renderEmpty$5$orgmajiroplayerMainActivity(view);
                }
            });
            LinearLayout.LayoutParams layoutParams = new LinearLayout.LayoutParams(-2, Ui.dp(this, 50.0f));
            layoutParams.setMargins(0, Ui.dp(this, 24.0f), 0, 0);
            linearLayout.addView(textViewButton, layoutParams);
        }
        this.page.addView(linearLayout, new LinearLayout.LayoutParams(-1, -2));
    }

    /* JADX INFO: renamed from: lambda$renderEmpty$5$org-majiro-player-MainActivity, reason: not valid java name */
    /* synthetic */ void m38lambda$renderEmpty$5$orgmajiroplayerMainActivity(View view) {
        choose();
    }

    private void renderGames(ArrayList<GameLibrary.Entry> arrayList) {
        int i;
        int i2 = getResources().getConfiguration().screenWidthDp;
        if (i2 >= 1180) {
            i = 5;
        } else if (i2 >= 820) {
            i = 4;
        } else if (i2 >= 560) {
            i = 3;
        } else {
            i = i2 >= 320 ? 2 : 1;
        }
        LinearLayout linearLayout = null;
        int i3 = 0;
        while (i3 < arrayList.size()) {
            int i4 = i3 % i;
            if (i4 == 0) {
                linearLayout = new LinearLayout(this);
                linearLayout.setOrientation(0);
                LinearLayout.LayoutParams layoutParams = new LinearLayout.LayoutParams(-1, -2);
                layoutParams.setMargins(Ui.dp(this, -6.0f), i3 == 0 ? 0 : Ui.dp(this, 6.0f), Ui.dp(this, -6.0f), 0);
                this.page.addView(linearLayout, layoutParams);
            }
            linearLayout.addView(gameCard(arrayList.get(i3)), cardParams());
            if (i4 == i - 1 || i3 == arrayList.size() - 1) {
                int i5 = (i - i4) - 1;
                for (int i6 = 0; i6 < i5; i6++) {
                    View view = new View(this);
                    view.setImportantForAccessibility(2);
                    linearLayout.addView(view, cardParams());
                }
            }
            i3++;
        }
    }

    private LinearLayout.LayoutParams cardParams() {
        LinearLayout.LayoutParams layoutParams = new LinearLayout.LayoutParams(0, -2, 1.0f);
        layoutParams.setMargins(Ui.dp(this, 6.0f), Ui.dp(this, 6.0f), Ui.dp(this, 6.0f), Ui.dp(this, 6.0f));
        return layoutParams;
    }

    private View gameCard(final GameLibrary.Entry entry) {
        LinearLayout linearLayout = new LinearLayout(this);
        linearLayout.setOrientation(1);
        linearLayout.setPadding(Ui.dp(this, 12.0f), Ui.dp(this, 12.0f), Ui.dp(this, 12.0f), Ui.dp(this, 15.0f));
        linearLayout.setBackground(Ui.ripple(this, this.colors, this.colors.surface, 20.0f, true));
        linearLayout.setElevation(Ui.dp(this, AppearanceSettings.isDark(this) ? 0.0f : 1.0f));
        linearLayout.setFocusable(true);
        linearLayout.setContentDescription(entry.title + "，开始游戏");
        FrameLayout frameLayout = new FrameLayout(this);
        frameLayout.setBackground(Ui.shape(this, this.colors.alternate, 14.0f));
        ImageView imageView = new ImageView(this);
        imageView.setScaleType(ImageView.ScaleType.FIT_CENTER);
        Bitmap bitmapArtwork = artwork(entry.icon);
        if (bitmapArtwork != null) {
            imageView.setImageBitmap(bitmapArtwork);
        } else {
            imageView.setImageDrawable(new DefaultGameIcon());
        }
        imageView.setImportantForAccessibility(2);
        frameLayout.addView(imageView, new FrameLayout.LayoutParams(Ui.dp(this, 88.0f), Ui.dp(this, 88.0f), 17));
        TextView textViewText = Ui.text(this, "•••", 14.0f, this.colors.muted, true);
        textViewText.setGravity(17);
        textViewText.setContentDescription("管理 " + entry.title);
        textViewText.setFocusable(true);
        textViewText.setBackground(Ui.ripple(this, this.colors, 0, 22.0f, false));
        textViewText.setOnClickListener(new View.OnClickListener() { // from class: org.majiro.player.MainActivity$$ExternalSyntheticLambda9
            @Override // android.view.View.OnClickListener
            public final void onClick(View view) {
                MainActivity.this.m28lambda$gameCard$6$orgmajiroplayerMainActivity(entry, view);
            }
        });
        frameLayout.addView(textViewText, new FrameLayout.LayoutParams(Ui.dp(this, 48.0f), Ui.dp(this, 48.0f), 53));
        linearLayout.addView(frameLayout, new LinearLayout.LayoutParams(-1, Ui.dp(this, 126.0f)));
        TextView textViewText2 = Ui.text(this, entry.title, 16.0f, this.colors.ink, true);
        textViewText2.setMaxLines(2);
        textViewText2.setMinLines(2);
        textViewText2.setEllipsize(TextUtils.TruncateAt.END);
        textViewText2.setGravity(48);
        textViewText2.setImportantForAccessibility(2);
        textViewText2.setPadding(Ui.dp(this, 4.0f), Ui.dp(this, 14.0f), Ui.dp(this, 4.0f), 0);
        linearLayout.addView(textViewText2);
        TextView textViewText3 = Ui.text(this, playedLabel(entry.lastPlayed), 12.0f, this.colors.muted, false);
        textViewText3.setImportantForAccessibility(2);
        textViewText3.setPadding(Ui.dp(this, 4.0f), Ui.dp(this, 7.0f), Ui.dp(this, 4.0f), 0);
        linearLayout.addView(textViewText3);
        linearLayout.setOnClickListener(new View.OnClickListener() { // from class: org.majiro.player.MainActivity$$ExternalSyntheticLambda10
            @Override // android.view.View.OnClickListener
            public final void onClick(View view) {
                MainActivity.this.m29lambda$gameCard$7$orgmajiroplayerMainActivity(entry, view);
            }
        });
        linearLayout.setOnLongClickListener(new View.OnLongClickListener() { // from class: org.majiro.player.MainActivity$$ExternalSyntheticLambda11
            @Override // android.view.View.OnLongClickListener
            public final boolean onLongClick(View view) {
                return MainActivity.this.m30lambda$gameCard$8$orgmajiroplayerMainActivity(entry, view);
            }
        });
        return linearLayout;
    }

    /* JADX INFO: renamed from: lambda$gameCard$6$org-majiro-player-MainActivity, reason: not valid java name */
    /* synthetic */ void m28lambda$gameCard$6$orgmajiroplayerMainActivity(GameLibrary.Entry entry, View view) {
        details(entry);
    }

    /* JADX INFO: renamed from: lambda$gameCard$7$org-majiro-player-MainActivity, reason: not valid java name */
    /* synthetic */ void m29lambda$gameCard$7$orgmajiroplayerMainActivity(GameLibrary.Entry entry, View view) {
        launch(entry);
    }

    /* JADX INFO: renamed from: lambda$gameCard$8$org-majiro-player-MainActivity, reason: not valid java name */
    /* synthetic */ boolean m30lambda$gameCard$8$orgmajiroplayerMainActivity(GameLibrary.Entry entry, View view) {
        details(entry);
        return true;
    }

    private Bitmap artwork(String str) {
        if (str == null || str.isEmpty() || !new File(str).isFile()) {
            return null;
        }
        Bitmap bitmap = this.artworkCache.get(str);
        if (bitmap != null) {
            return bitmap;
        }
        BitmapFactory.Options options = new BitmapFactory.Options();
        options.inJustDecodeBounds = true;
        BitmapFactory.decodeFile(str, options);
        BitmapFactory.Options options2 = new BitmapFactory.Options();
        options2.inSampleSize = 1;
        while (options.outWidth / (options2.inSampleSize * 2) >= 256 && options.outHeight / (options2.inSampleSize * 2) >= 256) {
            options2.inSampleSize *= 2;
        }
        Bitmap bitmapDecodeFile = BitmapFactory.decodeFile(str, options2);
        if (bitmapDecodeFile != null) {
            this.artworkCache.put(str, bitmapDecodeFile);
        }
        return bitmapDecodeFile;
    }

    private static String playedLabel(long j) {
        if (j <= 0) {
            return "尚未游玩";
        }
        Calendar calendar = Calendar.getInstance();
        Calendar calendar2 = Calendar.getInstance();
        calendar2.setTimeInMillis(j);
        if (calendar.get(1) == calendar2.get(1) && calendar.get(6) == calendar2.get(6)) {
            return "今天游玩";
        }
        calendar.add(6, -1);
        return (calendar.get(1) == calendar2.get(1) && calendar.get(6) == calendar2.get(6)) ? "昨天游玩" : new SimpleDateFormat("M月d日游玩", Locale.CHINA).format(new Date(j));
    }

    private void choose() {
        Intent intent = new Intent("android.intent.action.OPEN_DOCUMENT_TREE");
        intent.addFlags(193);
        startActivityForResult(intent, 1);
    }

    @Override // android.app.Activity
    protected void onActivityResult(int i, int i2, Intent intent) {
        super.onActivityResult(i, i2, intent);
        if (i != 1 || i2 != -1 || intent == null || intent.getData() == null) {
            return;
        }
        final Uri data = intent.getData();
        try {
            getContentResolver().takePersistableUriPermission(data, intent.getFlags() & 1);
        } catch (Exception e) {
            DiagnosticLog.write("permission", e.toString());
        }
        this.artworkCache.clear();
        this.busy = true;
        render();
        this.worker.execute(new Runnable() { // from class: org.majiro.player.MainActivity$$ExternalSyntheticLambda4
            @Override // java.lang.Runnable
            public final void run() {
                MainActivity.this.m31lambda$onActivityResult$10$orgmajiroplayerMainActivity(data);
            }
        });
    }

    /* JADX INFO: renamed from: lambda$onActivityResult$10$org-majiro-player-MainActivity, reason: not valid java name */
    /* synthetic */ void m31lambda$onActivityResult$10$orgmajiroplayerMainActivity(Uri uri) {
        try {
            final int size = this.library.addTree(uri).size();
            if (!this.destroyed) {
                runOnUiThread(new Runnable() { // from class: org.majiro.player.MainActivity$$ExternalSyntheticLambda3
                    @Override // java.lang.Runnable
                    public final void run() {
                        MainActivity.this.m32lambda$onActivityResult$9$orgmajiroplayerMainActivity(size);
                    }
                });
            }
        } catch (Exception e) {
            DiagnosticLog.failure("add directory", e);
            showError("未能添加游戏", e.getMessage());
        }
        this.busy = false;
        refresh();
    }

    /* JADX INFO: renamed from: lambda$onActivityResult$9$org-majiro-player-MainActivity, reason: not valid java name */
    /* synthetic */ void m32lambda$onActivityResult$9$orgmajiroplayerMainActivity(int i) {
        Toast.makeText(this, "发现 " + i + " 个游戏", 0).show();
    }

    private void launch(GameLibrary.Entry entry) {
        launch(entry, null);
    }

    private void launch(GameLibrary.Entry entry, String str) {
        boolean z;
        if (this.busy) {
            return;
        }
        try {
            Iterator<UriPermission> it = getContentResolver().getPersistedUriPermissions().iterator();
            while (true) {
                if (!it.hasNext()) {
                    z = false;
                    break;
                }
                UriPermission next = it.next();
                if (next.isReadPermission() && next.getUri().toString().equals(entry.tree)) {
                    z = true;
                    break;
                }
            }
            if (!z) {
                throw new IllegalStateException("文件夹授权已失效，请重新添加这个游戏目录。");
            }
            this.library.played(entry);
            DiagnosticLog.write("launch", entry.id + " " + entry.title);
            Intent intentPutExtra = new Intent(this, (Class<?>) GameActivity.class).putExtra("game_tree", entry.tree).putExtra("game_root", entry.root).putExtra("game_title", entry.title).putExtra("game_id", entry.id);
            if ("original".equals(str) || "local".equals(str)) {
                intentPutExtra.putExtra("diagnostic_mode", str);
            }
            startActivity(intentPutExtra);
        } catch (Exception e) {
            DiagnosticLog.failure("launch", e);
            showError("无法启动", e.getMessage());
        }
    }

    private void details(final GameLibrary.Entry entry) {
        new AlertDialog.Builder(this).setTitle(entry.title).setItems(new String[]{"重命名", "移出游戏库"}, new DialogInterface.OnClickListener() { // from class: org.majiro.player.MainActivity$$ExternalSyntheticLambda13
            @Override // android.content.DialogInterface.OnClickListener
            public final void onClick(DialogInterface dialogInterface, int i) {
                MainActivity.this.m27lambda$details$13$orgmajiroplayerMainActivity(entry, dialogInterface, i);
            }
        }).show();
    }

    /* JADX INFO: renamed from: lambda$details$13$org-majiro-player-MainActivity, reason: not valid java name */
    /* synthetic */ void m27lambda$details$13$orgmajiroplayerMainActivity(final GameLibrary.Entry entry, DialogInterface dialogInterface, int i) {
        if (i == 0) {
            final EditText editText = new EditText(this);
            editText.setText(entry.title);
            editText.setSingleLine(true);
            editText.setSelectAllOnFocus(true);
            editText.setPadding(Ui.dp(this, 16.0f), Ui.dp(this, 12.0f), Ui.dp(this, 16.0f), Ui.dp(this, 12.0f));
            FrameLayout frameLayout = new FrameLayout(this);
            FrameLayout.LayoutParams layoutParams = new FrameLayout.LayoutParams(-1, -2);
            layoutParams.setMargins(Ui.dp(this, 20.0f), Ui.dp(this, 8.0f), Ui.dp(this, 20.0f), 0);
            frameLayout.addView(editText, layoutParams);
            new AlertDialog.Builder(this).setTitle("游戏名称").setView(frameLayout).setNegativeButton("取消", (DialogInterface.OnClickListener) null).setPositiveButton("保存", new DialogInterface.OnClickListener() { // from class: org.majiro.player.MainActivity$$ExternalSyntheticLambda0
                @Override // android.content.DialogInterface.OnClickListener
                public final void onClick(DialogInterface dialogInterface2, int i2) {
                    MainActivity.this.m25lambda$details$11$orgmajiroplayerMainActivity(entry, editText, dialogInterface2, i2);
                }
            }).show();
            return;
        }
        new AlertDialog.Builder(this).setTitle("移出游戏库？").setMessage("游戏文件和存档会保留。").setNegativeButton("取消", (DialogInterface.OnClickListener) null).setPositiveButton("移出", new DialogInterface.OnClickListener() { // from class: org.majiro.player.MainActivity$$ExternalSyntheticLambda1
            @Override // android.content.DialogInterface.OnClickListener
            public final void onClick(DialogInterface dialogInterface2, int i2) {
                MainActivity.this.m26lambda$details$12$orgmajiroplayerMainActivity(entry, dialogInterface2, i2);
            }
        }).show();
    }

    /* JADX INFO: renamed from: lambda$details$11$org-majiro-player-MainActivity, reason: not valid java name */
    /* synthetic */ void m25lambda$details$11$orgmajiroplayerMainActivity(GameLibrary.Entry entry, EditText editText, DialogInterface dialogInterface, int i) {
        try {
            this.library.rename(entry, editText.getText().toString());
            render();
        } catch (Exception e) {
            showError("未能保存", e.getMessage());
        }
    }

    /* JADX INFO: renamed from: lambda$details$12$org-majiro-player-MainActivity, reason: not valid java name */
    /* synthetic */ void m26lambda$details$12$orgmajiroplayerMainActivity(GameLibrary.Entry entry, DialogInterface dialogInterface, int i) {
        try {
            this.library.remove(entry);
            render();
        } catch (Exception e) {
            showError("未能移除", e.getMessage());
        }
    }

    private void showError(final String str, final String str2) {
        if (this.destroyed) {
            return;
        }
        runOnUiThread(new Runnable() { // from class: org.majiro.player.MainActivity$$ExternalSyntheticLambda5
            @Override // java.lang.Runnable
            public final void run() {
                MainActivity.this.m39lambda$showError$14$orgmajiroplayerMainActivity(str, str2);
            }
        });
    }

    /* JADX INFO: renamed from: lambda$showError$14$org-majiro-player-MainActivity, reason: not valid java name */
    /* synthetic */ void m39lambda$showError$14$orgmajiroplayerMainActivity(String str, String str2) {
        if (this.destroyed) {
            return;
        }
        new AlertDialog.Builder(this).setTitle(str).setMessage(str2).setPositiveButton("知道了", (DialogInterface.OnClickListener) null).show();
    }

    @Override // android.app.Activity
    protected void onResume() {
        super.onResume();
        if (this.library == null) {
            return;
        }
        if (this.renderedDark != AppearanceSettings.isDark(this)) {
            recreate();
        } else {
            render();
        }
    }

    @Override // android.app.Activity, android.content.ComponentCallbacks
    public void onConfigurationChanged(Configuration configuration) {
        super.onConfigurationChanged(configuration);
        render();
    }

    @Override // android.app.Activity
    public void onDestroy() {
        this.destroyed = true;
        this.artworkCache.clear();
        this.worker.shutdownNow();
        super.onDestroy();
    }
}
