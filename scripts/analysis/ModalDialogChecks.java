package org.majiro.player;

/** Execute the production async-dialog ownership rules on a plain JVM. */
public final class ModalDialogChecks {
    private static int checks;
    private static void check(boolean value) { ++checks; if (!value) throw new AssertionError(checks); }
    public static void main(String[] args) {
        ModalDialogState state = new ModalDialogState();
        for (int flags : new int[] {0, 1, 4, 5, 16, 17, 20, 21, 32, 33, 36, 37, 48, 49, 52}) {
            state.reset();
            ModalDialogState.Request old = state.begin("old", flags, "message", "caption");
            check(state.visible() == null);
            state.foreground(true); check(state.visible() == old);
            state.foreground(false); check(state.visible() == null);
            check(state.poll("old").equals("pending"));
            state.foreground(true); check(state.visible() == old);
            int buttons = flags & 15;
            int answer = buttons == 4 ? 7 : buttons == 5 ? 2 : 1;
            state.complete(old, answer); check(state.visible() == null);
            state.complete(old, buttons == 4 ? 6 : buttons == 5 ? 4 : 1);
            check(state.poll("old").equals("result " + answer));
            ModalDialogState.Request current = state.begin("next", flags, "new", "title");
            state.complete(old, answer); check(state.poll("next").equals("pending"));
            check(!state.owns(old) && state.owns(current));
            state.fail(old, "stale"); check(state.poll("next").equals("pending"));
            state.close(); check(state.poll("next").startsWith("error "));
        }
        state.reset(); state.foreground(true);
        ModalDialogState.Request request = state.begin("id", 4, "yes/no", "title");
        try { state.complete(request, 1); throw new AssertionError(); }
        catch (IllegalArgumentException expected) { check(state.poll("id").equals("pending")); }
        try { state.begin("other", 0, "x", "y"); throw new AssertionError(); }
        catch (IllegalStateException expected) { check(state.owns(request)); }
        state.complete(request, 6);
        check(state.poll("id").equals("result 6"));
        try { state.poll("id"); throw new AssertionError(); }
        catch (IllegalStateException expected) { check(true); }
        ModalDialogState.Request cancelled=state.begin("cancelled",0,"message","title");
        check(!state.cancel("different")&&state.owns(cancelled));
        check(state.cancel("cancelled")&&!state.owns(cancelled));
        ModalDialogState.Request replacement=state.begin("replacement",0,"new","title");
        state.complete(cancelled,1);
        check(state.poll("replacement").equals("pending")&&state.owns(replacement));
        state.complete(replacement,1);
        check(state.poll("replacement").equals("result 1"));
        ModalDialogState.Request editor=state.begin("editor",65537,"prompt","value");
        state.completeData(editor,"first");
        state.complete(editor,1);state.completeData(editor,"second");
        check(state.poll("editor").equals("data 6669727374"));
        System.out.println("{\"status\":\"passed\",\"checks\":" + checks + "}");
    }
}
