package org.majiro.player;

/** Deterministic ownership for asynchronous native dialogs, independent of Android UI. */
public final class ModalDialogState {
    public static final class Request {
        public final String token, message, caption;
        public final int flags;
        public String draft;
        private Integer answer;
        private String data;
        private String error;
        Request(String token, int flags, String message, String caption) {
            this.token = token; this.flags = flags; this.message = message; this.caption = caption;
            this.draft = (flags >>> 16) == 5 ? "" : (flags >>> 16) == 1 ? caption : message;
        }
    }
    private Request current;
    private boolean foreground;
    public synchronized Request begin(String token, int flags, String message, String caption) {
        if (current != null) throw new IllegalStateException("dialog already pending");
        if (token.length() == 0 || message == null || caption == null)
            throw new IllegalArgumentException("invalid native dialog request");
        int buttons = flags & 15;
        if (buttons != 0 && buttons != 1 && buttons != 4 && buttons != 5)
            throw new IllegalArgumentException("unsupported native dialog buttons");
        current = new Request(token, flags, message, caption);
        return current;
    }
    public synchronized Request visible() {
        return foreground && current != null && current.answer == null && current.data == null &&
            current.error == null ? current : null;
    }
    public synchronized void foreground(boolean value) { foreground = value; }
    public synchronized boolean owns(Request request) { return current == request; }
    public synchronized void complete(Request request, int answer) {
        if (current != request || request.answer != null || request.data != null || request.error != null) return;
        int buttons = request.flags & 15;
        if ((buttons == 0 && answer != 1) || (buttons == 1 && answer != 1 && answer != 2)
            || (buttons == 4 && answer != 6 && answer != 7) || (buttons == 5 && answer != 4 && answer != 2))
            throw new IllegalArgumentException("answer does not match native dialog buttons");
        request.answer = answer;
    }
    public synchronized void fail(Request request, String error) {
        if (current == request && request.answer == null && request.data == null && request.error == null)
            request.error = error;
    }
    public synchronized void completeData(Request request, String value) {
        if (current != request || request.answer != null || request.data != null || request.error != null) return;
        if ((request.flags & 0xffff0000) == 0) throw new IllegalArgumentException("dialog is not an editor");
        byte[] bytes = value.getBytes(java.nio.charset.StandardCharsets.UTF_8);
        StringBuilder result = new StringBuilder();
        for (byte item : bytes) result.append(String.format(java.util.Locale.ROOT, "%02x", item & 255));
        request.data = result.toString();
    }
    public synchronized String poll(String token) {
        if (current == null || !current.token.equals(token))
            throw new IllegalStateException("unknown native dialog token");
        if (current.error != null) { String error = current.error; current = null; return "error " + error; }
        if (current.data != null) { String data = current.data; current = null; return "data " + data; }
        if (current.answer == null) return "pending";
        int answer = current.answer; current = null; return "result " + answer;
    }
    public synchronized void close() {
        foreground = false;
        if (current != null && current.answer == null && current.data == null)
            current.error = "Activity destroyed while dialog was pending";
    }
    public synchronized void reset() { current = null; foreground = false; }
    public synchronized boolean cancel(String token) {
        if(current==null||!current.token.equals(token))return false;
        current=null;return true;
    }
}
