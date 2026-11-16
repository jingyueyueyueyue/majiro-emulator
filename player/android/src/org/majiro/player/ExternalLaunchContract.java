package org.majiro.player;

/** Intent contract for launchers that already hold a SAF tree grant. */
final class ExternalLaunchContract {
    static final String ACTION_LAUNCH = "org.majiro.player.action.LAUNCH";
    static final String EXTRA_GAME_ROOT = "game_root";
    static final String EXTRA_GAME_TITLE = "game_title";
    static final String EXTRA_PROTOCOL_VERSION = "protocol_version";
    static final int PROTOCOL_VERSION = 1;

    private ExternalLaunchContract() {}
}
