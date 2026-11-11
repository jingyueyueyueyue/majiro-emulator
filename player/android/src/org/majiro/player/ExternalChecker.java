package org.majiro.player;

import android.app.Activity;
import android.content.Intent;
import android.net.Uri;

/** Real external disc-authenticator dispatch. No synthetic success response. */
public final class ExternalChecker {
    private static final int REQUEST_FAMILY = 0x6000;
    private static final int MESSAGE = 0x9004;
    public static final String ACTION = "org.majiro.player.action.AUTHENTICATE_DISC";
    public static final String COMPANION = "org.majiro.discchecker";
    private static String token;
    private static String state;
    private static int requestSequence;
    private static int activeRequest;

    private ExternalChecker() {}

    public static synchronized String begin(final Activity owner, final String requestToken,
                                             final Uri source) {
        if (token != null && "pending".equals(state))
            return "error checker request already active";
        final Intent request = new Intent(ACTION).setPackage(COMPANION);
        request.setData(source);
        request.addFlags(Intent.FLAG_GRANT_READ_URI_PERMISSION);
        request.putExtra("request_token", requestToken);
        request.putExtra("native_protocol_message", MESSAGE);
        if (request.resolveActivity(owner.getPackageManager()) == null)
            return "missing external disc authenticator: " + COMPANION;
        token = requestToken;
        state = "pending";
        requestSequence=(requestSequence+1)&0x1fff;
        final int requestCode=REQUEST_FAMILY|requestSequence;
        activeRequest=requestCode;
        owner.runOnUiThread(new Runnable() {
            @Override public void run() {
                synchronized(ExternalChecker.class){
                    if(activeRequest!=requestCode||!requestToken.equals(token)||!"pending".equals(state))return;
                }
                try {
                    owner.startActivityForResult(request, requestCode);
                } catch (Exception error) {
                    synchronized (ExternalChecker.class) {
                        if(activeRequest==requestCode&&requestToken.equals(token))
                            state = "error authenticator launch failed: " + error.getClass().getSimpleName();
                    }
                }
            }
        });
        return "pending";
    }

    public static synchronized String poll(String requestToken) {
        if (token == null || !token.equals(requestToken))
            return "error checker token mismatch";
        return state;
    }

    public static synchronized boolean result(int request, int status, Intent response) {
        if ((request & 0xe000) != REQUEST_FAMILY) return false;
        if(request!=activeRequest)return true;
        if (token == null || !"pending".equals(state)) return true;
        if (status != Activity.RESULT_OK || response == null) {
            state = "error disc authentication cancelled or rejected";
        } else if (!token.equals(response.getStringExtra("request_token"))) {
            state = "error authenticator response token mismatch";
        } else if (response.getIntExtra("native_protocol_message", -1) != MESSAGE
                || !response.getBooleanExtra("disc_authenticated", false)) {
            state = "error authenticator supplied no valid disc authentication";
        } else {
            state = "authenticated 9004";
        }
        return true;
    }

    public static synchronized void cancel() {
        if (token != null && "pending".equals(state))
            state = "error authenticator owner closed";
    }
    public static synchronized String cancel(String requestToken) {
        if (token == null || !token.equals(requestToken)) return "error checker token mismatch";
        if ("pending".equals(state)) state = "error authenticator owner context ended";
        return "OK";
    }
}
