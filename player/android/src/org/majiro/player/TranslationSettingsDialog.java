package org.majiro.player;

import android.app.Activity;
import android.app.AlertDialog;
import android.content.DialogInterface;
import android.graphics.Typeface;
import android.graphics.drawable.GradientDrawable;
import android.graphics.drawable.StateListDrawable;
import android.text.method.PasswordTransformationMethod;
import android.view.View;
import android.view.ViewGroup;
import android.view.Window;
import android.widget.AdapterView;
import android.widget.ArrayAdapter;
import android.widget.Button;
import android.widget.EditText;
import android.widget.LinearLayout;
import android.widget.ScrollView;
import android.widget.Spinner;
import android.widget.SpinnerAdapter;
import android.widget.TextView;
import java.util.ArrayList;
import java.util.Arrays;
import java.util.List;
import java.util.concurrent.ExecutorService;

/* JADX INFO: loaded from: classes.dex */
public final class TranslationSettingsDialog {
    private static final String MODEL_PLACEHOLDER = "请先获取模型列表";
    private static final String READY = "保存后应用翻译设置；切换本地汉化需重新进入游戏。";

    private TranslationSettingsDialog() {
    }

    private static TextView label(Activity activity, Ui.Colors colors, LinearLayout linearLayout, String str) {
        TextView textViewText = Ui.text(activity, str, 13.0f, colors.ink, true);
        LinearLayout.LayoutParams layoutParams = new LinearLayout.LayoutParams(-1, -2);
        layoutParams.setMargins(0, Ui.dp(activity, 18.0f), 0, Ui.dp(activity, 7.0f));
        linearLayout.addView(textViewText, layoutParams);
        return textViewText;
    }

    private static StateListDrawable fieldBackground(Activity activity, Ui.Colors colors) {
        GradientDrawable gradientDrawableOutlined = Ui.outlined(activity, colors, colors.alternate, 12.0f);
        GradientDrawable gradientDrawableShape = Ui.shape(activity, colors.alternate, 12.0f);
        gradientDrawableShape.setStroke(Ui.dp(activity, 2.0f), colors.accent);
        StateListDrawable stateListDrawable = new StateListDrawable();
        stateListDrawable.addState(new int[]{16842908}, gradientDrawableShape);
        stateListDrawable.addState(new int[0], gradientDrawableOutlined);
        return stateListDrawable;
    }

    private static EditText field(Activity activity, Ui.Colors colors, String str, String str2, int i) {
        EditText editText = new EditText(activity);
        editText.setText(str);
        editText.setHint(str2);
        editText.setSingleLine(true);
        editText.setInputType(i);
        editText.setTextSize(16.0f);
        editText.setTextColor(colors.ink);
        editText.setHintTextColor(colors.muted);
        editText.setGravity(16);
        editText.setMinimumHeight(Ui.dp(activity, 52.0f));
        editText.setPadding(Ui.dp(activity, 14.0f), Ui.dp(activity, 11.0f), Ui.dp(activity, 14.0f), Ui.dp(activity, 11.0f));
        editText.setBackground(fieldBackground(activity, colors));
        return editText;
    }

    private static void addField(LinearLayout linearLayout, EditText editText) {
        linearLayout.addView(editText, new LinearLayout.LayoutParams(-1, -2));
    }

    private static void styleButton(Activity activity, Ui.Colors colors, Button button) {
        button.setBackground(Ui.ripple(activity, colors, colors.surface, 12.0f, true));
        button.setTextColor(colors.accent);
        button.setTextSize(14.0f);
        button.setAllCaps(false);
        button.setMinimumHeight(Ui.dp(activity, 48.0f));
        button.setPadding(Ui.dp(activity, 14.0f), Ui.dp(activity, 10.0f), Ui.dp(activity, 14.0f), Ui.dp(activity, 10.0f));
    }

    /* JADX INFO: Access modifiers changed from: private */
    public static TextView spinnerText(View view, Ui.Colors colors) {
        TextView textView = (TextView) view;
        textView.setTextColor(colors.ink);
        textView.setTextSize(16.0f);
        return textView;
    }

    private static ArrayAdapter<String> adapter(Activity activity, final Ui.Colors colors, List<String> list) {
        ArrayAdapter<String> arrayAdapter = new ArrayAdapter<String>(activity, 17367048, list) { // from class: org.majiro.player.TranslationSettingsDialog.1
            @Override // android.widget.ArrayAdapter, android.widget.Adapter
            public View getView(int i, View view, ViewGroup viewGroup) {
                return TranslationSettingsDialog.spinnerText(super.getView(i, view, viewGroup), colors);
            }

            @Override // android.widget.ArrayAdapter, android.widget.BaseAdapter, android.widget.SpinnerAdapter
            public View getDropDownView(int i, View view, ViewGroup viewGroup) {
                TextView textViewSpinnerText = TranslationSettingsDialog.spinnerText(super.getDropDownView(i, view, viewGroup), colors);
                textViewSpinnerText.setBackgroundColor(colors.surface);
                return textViewSpinnerText;
            }
        };
        arrayAdapter.setDropDownViewResource(17367049);
        return arrayAdapter;
    }

    private static String selectedModel(Spinner spinner) {
        Object selectedItem = spinner.getSelectedItem();
        if (selectedItem == null) {
            return "";
        }
        String string = selectedItem.toString();
        return "请先获取模型列表".equals(string) ? "" : string;
    }

    private static String modeName(int i) {
        if (i == 3) {
            return "online";
        }
        if (i == 2) {
            return "local";
        }
        return i == 1 ? "original" : "auto";
    }

    /* JADX INFO: Access modifiers changed from: private */
    public static String modeDescription(int i) {
        if (i == 3) {
            return "游戏文本将发送给所填服务，可能产生费用；Key 加密保存在本机。";
        }
        if (i == 2) {
            return "使用当前游戏已有的本地汉化补丁。";
        }
        return i == 1 ? "显示游戏自带的日语文本。" : "根据游戏已有内容选择显示文本。";
    }

    private static boolean alive(Activity activity) {
        return (activity.isFinishing() || activity.isDestroyed()) ? false : true;
    }

    private static void post(final Activity activity, final AlertDialog alertDialog, final Runnable runnable) {
        if (alive(activity)) {
            activity.runOnUiThread(new Runnable() { // from class: org.majiro.player.TranslationSettingsDialog$$ExternalSyntheticLambda7
                @Override // java.lang.Runnable
                public final void run() {
                    TranslationSettingsDialog.lambda$post$0(activity, alertDialog, runnable);
                }
            });
        }
    }

    static /* synthetic */ void lambda$post$0(Activity activity, AlertDialog alertDialog, Runnable runnable) {
        if (!alive(activity) || !alertDialog.isShowing()) {
            return;
        }
        runnable.run();
    }

    public static void show(final Activity activity, final ExecutorService executorService, final Runnable runnable) {
        int i;
        if (alive(activity)) {
            final TranslationSettings translationSettingsLoad = TranslationSettings.load(activity);
            final Ui.Colors colors = Ui.colors(activity);
            TextView textViewText = Ui.text(activity, "文本与翻译", 22.0f, colors.ink, true);
            Ui.heading(textViewText);
            textViewText.setPadding(Ui.dp(activity, 24.0f), Ui.dp(activity, 22.0f), Ui.dp(activity, 24.0f), Ui.dp(activity, 8.0f));
            ScrollView scrollView = new ScrollView(activity);
            scrollView.setFillViewport(true);
            scrollView.setBackgroundColor(colors.surface);
            LinearLayout linearLayout = new LinearLayout(activity);
            linearLayout.setOrientation(1);
            linearLayout.setPadding(Ui.dp(activity, 24.0f), 0, Ui.dp(activity, 24.0f), Ui.dp(activity, 8.0f));
            scrollView.addView(linearLayout);
            label(activity, colors, linearLayout, "文本模式");
            final Spinner spinner = new Spinner(activity);
            spinner.setContentDescription("文本模式");
            spinner.setMinimumHeight(Ui.dp(activity, 48.0f));
            spinner.setPopupBackgroundDrawable(Ui.shape(activity, colors.surface, 12.0f));
            spinner.setAdapter((SpinnerAdapter) adapter(activity, colors, Arrays.asList("自动", "日语原文", "本地汉化", "在线翻译")));
            if (translationSettingsLoad.mode.equals("online")) {
                i = 3;
            } else if (translationSettingsLoad.mode.equals("local")) {
                i = 2;
            } else {
                i = translationSettingsLoad.mode.equals("original") ? 1 : 0;
            }
            spinner.setSelection(i);
            linearLayout.addView(spinner, new LinearLayout.LayoutParams(-1, -2));
            final TextView textViewText2 = Ui.text(activity, "", 14.0f, colors.muted, false);
            textViewText2.setLineSpacing(Ui.dp(activity, 2.0f), 1.0f);
            LinearLayout.LayoutParams layoutParams = new LinearLayout.LayoutParams(-1, -2);
            layoutParams.setMargins(0, Ui.dp(activity, 9.0f), 0, 0);
            linearLayout.addView(textViewText2, layoutParams);
            final LinearLayout linearLayout2 = new LinearLayout(activity);
            linearLayout2.setOrientation(1);
            linearLayout.addView(linearLayout2, new LinearLayout.LayoutParams(-1, -2));
            label(activity, colors, linearLayout2, "API 地址");
            final EditText editTextField = field(activity, colors, translationSettingsLoad.url, "https://example.com/v1", 17);
            addField(linearLayout2, editTextField);
            label(activity, colors, linearLayout2, "API Key");
            final EditText editTextField2 = field(activity, colors, translationSettingsLoad.key, "服务要求时填写", 129);
            editTextField2.setTransformationMethod(PasswordTransformationMethod.getInstance());
            LinearLayout linearLayout3 = new LinearLayout(activity);
            linearLayout3.setOrientation(0);
            linearLayout3.setGravity(16);
            linearLayout3.addView(editTextField2, new LinearLayout.LayoutParams(0, -2, 1.0f));
            final Button button = new Button(activity);
            button.setText("显示");
            button.setContentDescription("显示 API Key");
            button.setMinWidth(0);
            button.setMinimumWidth(0);
            styleButton(activity, colors, button);
            LinearLayout.LayoutParams layoutParams2 = new LinearLayout.LayoutParams(-2, -2);
            layoutParams2.setMargins(Ui.dp(activity, 9.0f), 0, 0, 0);
            linearLayout3.addView(button, layoutParams2);
            linearLayout2.addView(linearLayout3, new LinearLayout.LayoutParams(-1, -2));
            label(activity, colors, linearLayout2, "翻译模型");
            final Spinner spinner2 = new Spinner(activity);
            spinner2.setContentDescription("翻译模型");
            spinner2.setMinimumHeight(Ui.dp(activity, 48.0f));
            spinner2.setPopupBackgroundDrawable(Ui.shape(activity, colors.surface, 12.0f));
            ArrayList arrayList = new ArrayList();
            arrayList.add(translationSettingsLoad.model.isEmpty() ? "请先获取模型列表" : translationSettingsLoad.model);
            spinner2.setAdapter((SpinnerAdapter) adapter(activity, colors, arrayList));
            linearLayout2.addView(spinner2, new LinearLayout.LayoutParams(-1, -2));
            final Button button2 = new Button(activity);
            button2.setText("获取模型列表");
            styleButton(activity, colors, button2);
            LinearLayout.LayoutParams layoutParams3 = new LinearLayout.LayoutParams(-1, -2);
            layoutParams3.setMargins(0, Ui.dp(activity, 10.0f), 0, 0);
            linearLayout2.addView(button2, layoutParams3);
            label(activity, colors, linearLayout2, "上下文句数");
            final EditText editTextField3 = field(activity, colors, Integer.toString(translationSettingsLoad.context), "2–200，默认 20", 2);
            addField(linearLayout2, editTextField3);
            final TextView textViewText3 = Ui.text(activity, "保存后应用翻译设置；切换本地汉化需重新进入游戏。", 13.0f, colors.muted, false);
            textViewText3.setLineSpacing(Ui.dp(activity, 2.0f), 1.0f);
            LinearLayout.LayoutParams layoutParams4 = new LinearLayout.LayoutParams(-1, -2);
            layoutParams4.setMargins(0, Ui.dp(activity, 18.0f), 0, Ui.dp(activity, 14.0f));
            linearLayout.addView(textViewText3, layoutParams4);
            final AlertDialog alertDialogCreate = new AlertDialog.Builder(activity).setCustomTitle(textViewText).setView(scrollView).setNegativeButton("取消", (DialogInterface.OnClickListener) null).setPositiveButton("保存", (DialogInterface.OnClickListener) null).create();
            final boolean[] zArr = {false};
            button.setOnClickListener(new View.OnClickListener() { // from class: org.majiro.player.TranslationSettingsDialog$$ExternalSyntheticLambda4
                @Override // android.view.View.OnClickListener
                public final void onClick(View view) {
                    TranslationSettingsDialog.lambda$show$1(zArr, editTextField2, button, view);
                }
            });
            spinner.setOnItemSelectedListener(new AdapterView.OnItemSelectedListener() { // from class: org.majiro.player.TranslationSettingsDialog.2
                @Override // android.widget.AdapterView.OnItemSelectedListener
                public void onItemSelected(AdapterView<?> adapterView, View view, int i2, long j) {
                    linearLayout2.setVisibility(i2 == 3 ? 0 : 8);
                    textViewText2.setText(TranslationSettingsDialog.modeDescription(i2));
                    textViewText3.setText("保存后应用翻译设置；切换本地汉化需重新进入游戏。");
                }

                @Override // android.widget.AdapterView.OnItemSelectedListener
                public void onNothingSelected(AdapterView<?> adapterView) {
                }
            });
            textViewText2.setText(modeDescription(spinner.getSelectedItemPosition()));
            linearLayout2.setVisibility(spinner.getSelectedItemPosition() == 3 ? 0 : 8);
            button2.setOnClickListener(new View.OnClickListener() { // from class: org.majiro.player.TranslationSettingsDialog$$ExternalSyntheticLambda5
                @Override // android.view.View.OnClickListener
                public final void onClick(View view) {
                    TranslationSettingsDialog.lambda$show$5(editTextField, editTextField2, button2, textViewText3, executorService, activity, alertDialogCreate, spinner2, colors, spinner, view);
                }
            });
            alertDialogCreate.setOnShowListener(new DialogInterface.OnShowListener() { // from class: org.majiro.player.TranslationSettingsDialog$$ExternalSyntheticLambda6
                @Override // android.content.DialogInterface.OnShowListener
                public final void onShow(DialogInterface dialogInterface) {
                    TranslationSettingsDialog.lambda$show$7(alertDialogCreate, activity, colors, spinner, translationSettingsLoad, editTextField, spinner2, editTextField3, editTextField2, runnable, textViewText3, dialogInterface);
                }
            });
            alertDialogCreate.show();
        }
    }

    static /* synthetic */ void lambda$show$1(boolean[] zArr, EditText editText, Button button, View view) {
        zArr[0] = !zArr[0];
        int selectionStart = editText.getSelectionStart();
        int selectionEnd = editText.getSelectionEnd();
        editText.setTransformationMethod(zArr[0] ? null : PasswordTransformationMethod.getInstance());
        if (selectionStart >= 0 && selectionEnd >= 0) {
            int length = editText.getText().length();
            editText.setSelection(Math.min(selectionStart, length), Math.min(selectionEnd, length));
        }
        button.setText(zArr[0] ? "隐藏" : "显示");
        button.setContentDescription(zArr[0] ? "隐藏 API Key" : "显示 API Key");
    }

    static /* synthetic */ void lambda$show$5(EditText editText, EditText editText2, final Button button, final TextView textView, ExecutorService executorService, final Activity activity, final AlertDialog alertDialog, final Spinner spinner, final Ui.Colors colors, final Spinner spinner2, View view) {
        final String strTrim = editText.getText().toString().trim();
        final String string = editText2.getText().toString();
        button.setEnabled(false);
        textView.setText("正在获取模型…");
        try {
            executorService.execute(new Runnable() { // from class: org.majiro.player.TranslationSettingsDialog$$ExternalSyntheticLambda0
                @Override // java.lang.Runnable
                public final void run() {
                    TranslationSettingsDialog.lambda$show$4(strTrim, string, activity, alertDialog, spinner, colors, button, spinner2, textView);
                }
            });
        } catch (RuntimeException e) {
            button.setEnabled(true);
            textView.setText("暂时无法获取模型，请稍后重试。");
        }
    }

    static /* synthetic */ void lambda$show$4(String str, String str2, final Activity activity, AlertDialog alertDialog, final Spinner spinner, final Ui.Colors colors, final Button button, final Spinner spinner2, final TextView textView) {
        try {
            final ArrayList<String> arrayListModels = TranslationService.models(str, str2);
            post(activity, alertDialog, new Runnable() { // from class: org.majiro.player.TranslationSettingsDialog$$ExternalSyntheticLambda2
                @Override // java.lang.Runnable
                public final void run() {
                    TranslationSettingsDialog.lambda$show$2(spinner, activity, colors, arrayListModels, button, spinner2, textView);
                }
            });
        } catch (Exception e) {
            post(activity, alertDialog, new Runnable() { // from class: org.majiro.player.TranslationSettingsDialog$$ExternalSyntheticLambda3
                @Override // java.lang.Runnable
                public final void run() {
                    TranslationSettingsDialog.lambda$show$3(button, spinner2, e, textView);
                }
            });
        }
    }

    static /* synthetic */ void lambda$show$2(Spinner spinner, Activity activity, Ui.Colors colors, ArrayList arrayList, Button button, Spinner spinner2, TextView textView) {
        String strSelectedModel = selectedModel(spinner);
        spinner.setAdapter((SpinnerAdapter) adapter(activity, colors, arrayList));
        int iIndexOf = arrayList.indexOf(strSelectedModel);
        if (iIndexOf >= 0) {
            spinner.setSelection(iIndexOf);
        }
        button.setEnabled(true);
        if (spinner2.getSelectedItemPosition() == 3) {
            textView.setText("已获取 " + arrayList.size() + " 个模型");
        }
    }

    static /* synthetic */ void lambda$show$3(Button button, Spinner spinner, Exception exc, TextView textView) {
        button.setEnabled(true);
        if (spinner.getSelectedItemPosition() == 3) {
            String str = "";
            String message = exc instanceof IllegalStateException ? exc.getMessage() : "";
            StringBuilder sbAppend = new StringBuilder().append("获取失败，请检查地址、Key 和网络。");
            if (message != null && !message.isEmpty()) {
                str = " " + message;
            }
            textView.setText(sbAppend.append(str).toString());
        }
    }

    static /* synthetic */ void lambda$show$7(final AlertDialog alertDialog, final Activity activity, Ui.Colors colors, final Spinner spinner, final TranslationSettings translationSettings, final EditText editText, final Spinner spinner2, final EditText editText2, final EditText editText3, final Runnable runnable, final TextView textView, DialogInterface dialogInterface) {
        Window window = alertDialog.getWindow();
        if (window != null) {
            window.setBackgroundDrawable(Ui.shape(activity, colors.surface, 22.0f));
        }
        Button button = alertDialog.getButton(-2);
        Button button2 = alertDialog.getButton(-1);
        button.setTextColor(colors.muted);
        button.setAllCaps(false);
        button.setTextSize(14.0f);
        button2.setTextColor(colors.accent);
        button2.setAllCaps(false);
        button2.setTextSize(14.0f);
        button2.setTypeface(Typeface.create("sans-serif-medium", 0));
        button2.setOnClickListener(new View.OnClickListener() { // from class: org.majiro.player.TranslationSettingsDialog$$ExternalSyntheticLambda1
            @Override // android.view.View.OnClickListener
            public final void onClick(View view) {
                TranslationSettingsDialog.lambda$show$6(spinner, activity, translationSettings, editText, spinner2, editText2, editText3, alertDialog, runnable, textView, view);
            }
        });
    }

    static /* synthetic */ void lambda$show$6(Spinner spinner, Activity activity, TranslationSettings translationSettings, EditText editText, Spinner spinner2, EditText editText2, EditText editText3, AlertDialog alertDialog, Runnable runnable, TextView textView, View view) {
        try {
            int selectedItemPosition = spinner.getSelectedItemPosition();
            String strModeName = modeName(selectedItemPosition);
            if (selectedItemPosition != 3) {
                TranslationSettings.save(activity, strModeName, translationSettings.url, translationSettings.key, translationSettings.model, translationSettings.context);
            } else {
                String strTrim = editText.getText().toString().trim();
                String strSelectedModel = selectedModel(spinner2);
                if (strTrim.isEmpty()) {
                    throw new IllegalArgumentException("请填写 API 地址");
                }
                if (strSelectedModel.isEmpty()) {
                    throw new IllegalArgumentException("请先获取并选择模型");
                }
                try {
                    int i = Integer.parseInt(editText2.getText().toString().trim());
                    if (i < 2 || i > 200) {
                        throw new IllegalArgumentException("上下文句数需在 2 到 200 之间");
                    }
                    TranslationSettings.save(activity, "online", strTrim, editText3.getText().toString(), strSelectedModel, i);
                } catch (NumberFormatException e) {
                    throw new IllegalArgumentException("请填写上下文句数");
                }
            }
            alertDialog.dismiss();
            runnable.run();
        } catch (Exception e2) {
            String message = e2.getMessage();
            if (message == null || message.trim().isEmpty()) {
                message = "保存失败，请检查设置";
            }
            textView.setText(message);
        }
    }
}
