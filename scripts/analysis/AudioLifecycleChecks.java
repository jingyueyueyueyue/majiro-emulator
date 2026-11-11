package org.majiro.player;

import java.util.HashMap;
import java.util.Map;

/** Runs the production ownership state machine against deterministic players. */
public final class AudioLifecycleChecks {
    private static int checks;
    private static final class Player {
        boolean playing = true;
        boolean released;
        boolean completesDuringPause;
        int pauses, starts;
        // Force equal values to catch accidental equality-based ownership.
        @Override public boolean equals(Object other) { return other instanceof Player; }
        @Override public int hashCode() { return 0; }
    }
    private static final class Fixture {
        final Map<String, Player> players = new HashMap<String, Player>();
        final AudioLifecycle<Player> state = new AudioLifecycle<Player>(players,
            new AudioLifecycle.Transport<Player>() {
                @Override public boolean playing(Player p) {
                    if (p.released) throw new IllegalStateException("released player queried");
                    return p.playing;
                }
                @Override public boolean pause(Player p) {
                    if (p.released) throw new IllegalStateException("released player paused");
                    if (p.completesDuringPause) { p.playing=false;return false; }
                    p.playing = false; p.pauses++;
                    return true;
                }
                @Override public void start(Player p) {
                    if (p.released) throw new IllegalStateException("released player resumed");
                    p.playing = true; p.starts++;
                }
            });
        Player add(String id) {
            Player p = new Player(); players.put(id,p); state.started(id); return p;
        }
        void remove(String id) {
            Player p = players.remove(id);
            state.removed(p); p.released = true;
        }
        void handoff(String id, Player next) {
            Player old = players.put(id,next); state.transfer(old,next); old.released = true;
        }
    }
    private static void check(boolean condition, String why) {
        checks++;
        if (!condition) throw new AssertionError(why);
    }
    public static void main(String[] ignored) {
        Fixture f = new Fixture();
        check(!f.state.pause("missing") && !f.state.resume("missing"),"absent channel");
        Player first = f.add("music");
        check(f.state.pause("music") && !first.playing && first.pauses==1,"script pause");
        check(f.state.active("music"),"paused source channel remains active");
        check(f.state.pause("music") && first.pauses==1,"repeated script pause");
        check(f.state.resume("music") && first.playing && first.starts==1,"script resume");
        check(f.state.resume("music") && first.starts==1,"resume does not restart active audio");
        first.playing = false;
        check(!f.state.active("music") && !f.state.pause("music"),"completed channel remains completed");
        check(!f.state.resume("music") && first.starts==1,"resume does not restart completed audio");

        f = new Fixture();
        Player restored = new Player(); restored.playing = false;
        f.players.put("music",restored); f.state.createdPaused("music");
        check(f.state.active("music") && restored.starts==0 && restored.pauses==0,
            "paused save creates an active channel without starting audio");
        check(f.state.pause("music") && restored.pauses==0,"paused replay verification is idempotent");
        check(f.state.resume("music") && restored.starts==1,"paused save resumes explicitly");

        for (boolean manual : new boolean[]{false,true}) {
            for (boolean background : new boolean[]{false,true}) {
                f = new Fixture();
                Player seeking = new Player(); seeking.playing = false;
                f.players.put("music",seeking); f.state.beginSeek("music",manual);
                if (background) f.state.background();
                check(f.state.active("music") && seeking.starts==0,"seeking channel is active without sound");
                check(f.state.seekComplete("music",seeking),"current seek completion accepted");
                check(seeking.playing == !(manual||background),"seek completion respects both pause reasons");
                f.state.foreground();
                check(seeking.playing == !manual,"foreground after seek respects script pause");
                if (manual) check(f.state.resume("music") && seeking.starts==1,"paused seek resumes once");
            }
        }
        f = new Fixture();
        Player seeking = new Player(); seeking.playing = false;
        f.players.put("music",seeking); f.state.beginSeek("music",false);
        f.state.background(); f.state.foreground();
        check(f.state.seekComplete("music",seeking) && seeking.starts==1,
            "foreground before seek completion preserves pending start");
        check(!f.state.seekComplete("music",seeking) && seeking.starts==1,"duplicate seek callback ignored");
        f.state.removed(seeking);
        Player secondSeek = new Player(); secondSeek.playing=false;
        f.players.put("music",secondSeek);
        f.state.beginSeek("music",true);
        f.state.resume("music");
        check(secondSeek.starts==0,"resume cannot start a pending seek");
        f.state.removed(secondSeek); f.players.remove("music"); secondSeek.released=true;
        check(!f.state.seekComplete("music",secondSeek),"stale seek callback cannot revive a stopped owner");

        f = new Fixture();
        Player music = f.add("music"), voice = f.add("voice");
        f.state.pause("music");
        f.state.background(); f.state.background();
        check(!voice.playing && voice.pauses==1,"duplicate background notification");
        f.state.foreground(); f.state.foreground();
        check(!music.playing && voice.playing && voice.starts==1,"foreground preserves script pause");
        check(f.state.resume("music") && music.starts==1,"explicit resume after foreground");

        f.state.background();
        f.state.pause("voice");
        f.state.foreground();
        check(!voice.playing && music.playing,"script pause while background survives foreground");
        f.state.background();
        check(f.state.resume("voice") && !voice.playing,"resume waits for foreground");
        f.state.foreground();
        check(voice.playing && voice.starts==2,"resumed background channel starts once");

        f = new Fixture();
        f.state.background();
        Player delayed = f.add("music");
        check(!delayed.playing && f.state.active("music"),"new audio while background");
        f.state.foreground();
        check(delayed.playing && delayed.starts==1,"new background audio resumes");

        f = new Fixture();
        Player old = f.add("music");
        f.state.background();
        f.players.remove("music");
        old.released = true; // Even a missed cleanup must not revive an old owner.
        Player replacement = f.add("music");
        f.state.foreground();
        check(replacement.playing && replacement.starts==1,"replacement ownership uses identity");

        for (boolean manual : new boolean[]{false,true}) {
            for (boolean background : new boolean[]{false,true}) {
                f = new Fixture();
                f.add("music");
                if (manual) f.state.pause("music");
                if (background) f.state.background();
                Player next = new Player();
                f.handoff("music",next);
                check(next.playing == !(manual||background),"intro handoff preserves suspension");
                f.state.foreground();
                check(next.playing == !manual,"handoff foreground respects script pause");
                if (manual) check(f.state.resume("music") && next.playing,"handoff explicit resume");
            }
        }

        f = new Fixture();
        Player stopped = f.add("music");
        f.state.background(); f.remove("music"); f.state.foreground();
        check(stopped.starts==0,"stopped channel cannot resume");
        f = new Fixture();
        Player ending = f.add("music"); ending.completesDuringPause=true;
        check(!f.state.pause("music") && !f.state.active("music"),"completion racing script pause stays completed");
        f.state.background(); f.state.foreground();
        check(ending.starts==0,"completed pause race cannot restart on foreground");
        ending = f.add("music"); ending.completesDuringPause=true;
        f.state.background(); f.state.foreground();
        check(ending.starts==0 && !f.state.active("music"),"completion racing background stays completed");
        f.state.reset();
        check(!f.state.isBackground() && !f.state.active("music"),"closed session resets");
        System.out.println("{\"status\":\"passed\",\"checks\":"+checks+"}");
    }
}
