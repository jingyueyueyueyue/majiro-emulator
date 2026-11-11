package org.majiro.player;

import java.util.ArrayList;
import java.util.Collections;
import java.util.IdentityHashMap;
import java.util.Map;
import java.util.Set;

/** Per-channel script pause and application suspension share player ownership. */
public final class AudioLifecycle<P> {
    public interface Transport<P> {
        boolean playing(P player);
        boolean pause(P player);
        void start(P player);
    }

    private final Map<String, P> players;
    private final Transport<P> transport;
    private final Set<P> scriptPaused =
        Collections.newSetFromMap(new IdentityHashMap<P, Boolean>());
    private final Set<P> backgroundPaused =
        Collections.newSetFromMap(new IdentityHashMap<P, Boolean>());
    private final Set<P> pendingStart =
        Collections.newSetFromMap(new IdentityHashMap<P, Boolean>());
    private boolean background;

    public AudioLifecycle(Map<String, P> players, Transport<P> transport) {
        this.players = players;
        this.transport = transport;
    }

    public boolean active(String id) {
        P player = players.get(id);
        return player != null && (transport.playing(player)
            || scriptPaused.contains(player) || backgroundPaused.contains(player)
            || pendingStart.contains(player));
    }
    public boolean isBackground() { return background; }

    private boolean owns(P player) {
        for (P candidate : players.values()) if (candidate == player) return true;
        return false;
    }

    public boolean pause(String id) {
        P player = players.get(id);
        if (player == null) return false;
        if (scriptPaused.contains(player)) return true;
        if (transport.playing(player)) {
            if (!transport.pause(player)) return false;
        }
        else if (!backgroundPaused.contains(player) && !pendingStart.contains(player)) return false;
        backgroundPaused.remove(player);
        scriptPaused.add(player);
        return true;
    }

    public boolean resume(String id) {
        P player = players.get(id);
        if (player == null) return false;
        if (scriptPaused.remove(player)) {
            if (pendingStart.contains(player)) return true;
            if (background) backgroundPaused.add(player);
            else transport.start(player);
        }
        return active(id);
    }

    public void started(String id) {
        P player = players.get(id);
        if (player == null) throw new IllegalStateException("audio channel has no owner");
        if (background) {
            if (transport.playing(player) && !transport.pause(player)) return;
            backgroundPaused.add(player);
        }
    }
    public void createdPaused(String id) {
        P player = players.get(id);
        if (player == null) throw new IllegalStateException("paused audio channel has no owner");
        scriptPaused.add(player);
        backgroundPaused.remove(player);
    }
    public void beginSeek(String id, boolean paused) {
        P player = players.get(id);
        if (player == null) throw new IllegalStateException("seeking audio channel has no owner");
        pendingStart.add(player);
        if (paused) scriptPaused.add(player);
    }

    public boolean seekComplete(String id, P player) {
        if (players.get(id) != player || !pendingStart.remove(player)) return false;
        if (scriptPaused.contains(player)) return true;
        if (background) backgroundPaused.add(player);
        else transport.start(player);
        return true;
    }

    public void removed(P player) {
        scriptPaused.remove(player);
        backgroundPaused.remove(player);
        pendingStart.remove(player);
    }

    public void transfer(P previous, P next) {
        boolean manual = scriptPaused.remove(previous);
        backgroundPaused.remove(previous);
        pendingStart.remove(previous);
        if (manual) scriptPaused.add(next);
        if (background || manual) {
            if (transport.playing(next) && !transport.pause(next)) {
                scriptPaused.remove(next);return;
            }
            if (background && !manual) backgroundPaused.add(next);
        }
    }

    public void background() {
        if (background) return;
        background = true;
        RuntimeException failure = null;
        for (P player : players.values()) {
            try {
                if (!scriptPaused.contains(player) && transport.playing(player)
                    && transport.pause(player)) backgroundPaused.add(player);
            } catch (RuntimeException error) { if (failure == null) failure = error; }
        }
        if (failure != null) throw failure;
    }

    public void foreground() {
        if (!background) return;
        background = false;
        ArrayList<P> pending = new ArrayList<P>(backgroundPaused);
        backgroundPaused.clear();
        RuntimeException failure = null;
        for (P player : pending) {
            if (!owns(player) || scriptPaused.contains(player)) continue;
            try { transport.start(player); }
            catch (RuntimeException error) { if (failure == null) failure = error; }
        }
        if (failure != null) throw failure;
    }

    public void reset() {
        scriptPaused.clear();
        backgroundPaused.clear();
        pendingStart.clear();
        background = false;
    }
}
