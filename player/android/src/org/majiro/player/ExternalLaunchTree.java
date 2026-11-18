package org.majiro.player;

import android.content.ContentResolver;
import android.content.Context;
import android.content.UriPermission;
import android.database.Cursor;
import android.net.Uri;
import android.os.Build;
import android.os.Environment;
import android.os.storage.StorageManager;
import android.os.storage.StorageVolume;
import android.provider.DocumentsContract;

import java.io.File;
import java.io.IOException;

final class ExternalLaunchTree {
    private static final String AUTHORITY = "com.android.externalstorage.documents";

    final Uri uri;
    final String documentId;

    private ExternalLaunchTree(Uri uri, String documentId) {
        this.uri = uri;
        this.documentId = documentId;
    }

    static ExternalLaunchTree find(Context context, File directory) {
        ContentResolver resolver = context.getContentResolver();
        for (UriPermission grant : resolver.getPersistedUriPermissions()) {
            Uri tree = grant.getUri();
            if (!grant.isReadPermission() || !"content".equals(tree.getScheme())
                    || !AUTHORITY.equals(tree.getAuthority()) || !DocumentsContract.isTreeUri(tree)) {
                continue;
            }
            try {
                String grantId = DocumentsContract.getTreeDocumentId(tree);
                int separator = grantId.indexOf(':');
                if (separator <= 0) continue;
                File volume = volumeRoot(context, grantId.substring(0, separator));
                if (volume == null) continue;
                String document = documentFor(volume, directory, grantId);
                if (document == null) continue;
                Uri documentUri = DocumentsContract.buildDocumentUriUsingTree(tree, document);
                try (Cursor cursor = resolver.query(documentUri,
                        new String[]{DocumentsContract.Document.COLUMN_MIME_TYPE}, null, null, null)) {
                    if (cursor != null && cursor.moveToFirst()
                            && DocumentsContract.Document.MIME_TYPE_DIR.equals(cursor.getString(0))) {
                        return new ExternalLaunchTree(tree, document);
                    }
                }
            } catch (IOException | IllegalArgumentException | SecurityException unavailable) {
                continue;
            }
        }
        return null;
    }

    private static File volumeRoot(Context context, String volumeId) {
        if ("primary".equals(volumeId)) return Environment.getExternalStorageDirectory();
        if (Build.VERSION.SDK_INT < 30) return null;
        StorageManager storage = context.getSystemService(StorageManager.class);
        if (storage == null) return null;
        for (StorageVolume volume : storage.getStorageVolumes()) {
            if (volumeId.equalsIgnoreCase(volume.getUuid())) return volume.getDirectory();
        }
        return null;
    }

    private static String documentFor(File volume, File directory, String grantId) throws IOException {
        String root = volume.getCanonicalPath();
        String path = directory.getPath();
        String prefix = root.endsWith(File.separator) ? root : root + File.separator;
        String relative;
        if (path.equals(root)) {
            relative = "";
        } else if (path.startsWith(prefix)) {
            relative = path.substring(prefix.length()).replace(File.separatorChar, '/');
        } else {
            return null;
        }
        String document = grantId.substring(0, grantId.indexOf(':') + 1) + relative;
        return document.equals(grantId) || document.startsWith(grantId.endsWith(":") ? grantId : grantId + "/")
                ? document : null;
    }
}
