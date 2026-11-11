package org.majiro.player;

import android.app.Activity;
import android.app.Instrumentation;
import android.content.Context;
import android.os.Bundle;
import android.os.ParcelFileDescriptor;
import java.io.File;
import java.io.FileOutputStream;
import java.io.InputStream;
import java.io.ByteArrayOutputStream;
import org.json.JSONArray;
import org.json.JSONObject;

/** Native ABI and malformed-input checks on the actual Android runtime. */
public final class CoreInstrumentation extends Instrumentation {
    @Override public void onCreate(Bundle arguments) {
        super.onCreate(arguments);
        start();
    }

    @Override public void onStart() {
        Bundle result = new Bundle();
        JSONArray cases = new JSONArray();
        try {
            Context context = getTargetContext();
            for (int version = 1; version <= 4; ++version) {
                String asset = "core-test-v" + version + ".arc";
                File file = new File(context.getCacheDir(), asset);
                try {
                    try (InputStream input = context.getAssets().open(asset);
                         FileOutputStream output = new FileOutputStream(file)) {
                        byte[] buffer = new byte[8192];
                        int count;
                        while ((count = input.read(buffer)) != -1) output.write(buffer, 0, count);
                    }
                    JSONObject report;
                    try (ParcelFileDescriptor fd = ParcelFileDescriptor.open(
                            file, ParcelFileDescriptor.MODE_READ_ONLY)) {
                        report = new JSONObject(NativeCore.inspectArchive(fd.getFd(), fd.getStatSize()));
                    }
                    if (version <= 3) {
                        if (!report.getString("status").equals("passed")
                                || report.getInt("arc_version") != version
                                || report.getInt("parsed_scripts") != 2
                                || report.getInt("scripts_without_main") != 1)
                            throw new IllegalStateException("native fixture mismatch: " + report);
                    } else if (!report.getString("status").equals("failed")) {
                        throw new IllegalStateException("corrupted archive was accepted");
                    }
                    cases.put(report);
                } finally { file.delete(); }
            }
            String[] images = {"core-image-tc00.rct", "core-image-ts00.rct",
                "core-image-rc8.rc8", "core-image-bad.rct"};
            for (int i = 0; i < images.length; ++i) {
                ByteArrayOutputStream bytes = new ByteArrayOutputStream();
                try (InputStream input = context.getAssets().open(images[i])) {
                    byte[] buffer = new byte[8192];
                    int count;
                    while ((count = input.read(buffer)) != -1) bytes.write(buffer, 0, count);
                }
                byte[] password = i == 1 ? "fixture-key".getBytes("US-ASCII") : null;
                JSONObject image = new JSONObject(NativeCore.inspectImage(bytes.toByteArray(), password));
                if (i == 3) {
                    if (!image.getString("status").equals("failed"))
                        throw new IllegalStateException("corrupted image was accepted");
                } else {
                    String expected = i == 2 ? "000000ff010101ff" : "030201ff060504ff";
                    if (!image.getString("status").equals("passed")
                            || image.getInt("width") != 2 || image.getInt("height") != 1
                            || !image.getString("pixel_prefix").equals(expected))
                        throw new IllegalStateException("native image fixture mismatch: " + image);
                }
                cases.put(image);
            }
            String[] saves = {"core-save-system.mss", "core-save-readmarks.mss",
                "core-save-story103.mss", "core-save-story105.mss", "core-save-bad.mss"};
            for (int i = 0; i < saves.length; ++i) {
                File file = new File(context.getCacheDir(), saves[i]);
                try {
                    try (InputStream input = context.getAssets().open(saves[i]);
                         FileOutputStream output = new FileOutputStream(file)) {
                        byte[] buffer = new byte[8192];
                        int count;
                        while ((count = input.read(buffer)) != -1) output.write(buffer, 0, count);
                    }
                    JSONObject decoded;
                    try (ParcelFileDescriptor fd = ParcelFileDescriptor.open(file, ParcelFileDescriptor.MODE_READ_ONLY)) {
                        decoded = new JSONObject(NativeCore.inspectNativeSave(fd.getFd(), fd.getStatSize(), i == 1));
                    }
                    if (i == 4) {
                        if (!decoded.getString("status").equals("failed"))
                            throw new IllegalStateException("corrupted native save was accepted");
                    } else {
                        if (!decoded.getString("status").equals("passed")
                                || decoded.getBoolean("native_story_restore_supported"))
                            throw new IllegalStateException("native save fixture status mismatch");
                        if (i == 0) {
                            JSONArray values = decoded.getJSONArray("variables");
                            if (decoded.getInt("variable_count") != 11
                                    || values.getJSONObject(0).getInt("value") != -1
                                    || !values.getJSONObject(1).getJSONObject("value")
                                        .getString("float32_bits").equals("80000000"))
                                throw new IllegalStateException("native save values mismatch");
                        } else if (i == 1) {
                            JSONArray lines = decoded.getJSONArray("records").getJSONObject(0)
                                .getJSONArray("marked_line_ids");
                            if (decoded.getInt("record_count") != 1 || lines.length() != 4
                                    || lines.getInt(0) != 0 || lines.getInt(1) != 7
                                    || lines.getInt(2) != 14 || lines.getInt(3) != 16)
                                throw new IllegalStateException("native readmark bit indexes mismatch");
                        } else {
                            if (!decoded.getString("format").equals(i == 2 ? "MajiroSavV1.103" : "MajiroSavV1.105")
                                    || decoded.getBoolean("script_identity_verified")
                                    || decoded.getBoolean("resources_verified")
                                    || decoded.getJSONArray("frames").length() != 1
                                    || decoded.getJSONArray("groups").getJSONObject(0)
                                        .getJSONObject("common_value_view").getJSONArray("variables")
                                        .getJSONObject(0).getInt("value") != 42)
                                throw new IllegalStateException("native story inspection fixture mismatch");
                        }
                    }
                    cases.put(decoded);
                } finally { file.delete(); }
            }
            result.putString("stream", "Majiro native core: V1/V2/V3, MJO, RCT, encrypted TS00, RC8, native saves and corruption checks passed.\n");
            result.putString("cases", cases.toString());
            finish(Activity.RESULT_OK, result);
        } catch (Throwable error) {
            result.putString("stream", "Majiro native core FAILED: " + error.toString() + "\n");
            result.putString("cases", cases.toString());
            finish(Activity.RESULT_CANCELED, result);
        }
    }
}
