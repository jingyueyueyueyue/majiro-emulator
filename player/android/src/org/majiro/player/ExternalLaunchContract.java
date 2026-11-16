package org.majiro.player;

/** TyranorNext third-party runtime module launch contract. */
final class ExternalLaunchContract {
    static final String ACTION_LAUNCH = "com.tyranor.runtime.action.LAUNCH";
    static final String EXTRA_FOLDER_PATH = "com.tyranor.runtime.extra.FOLDER_PATH";
    static final String EXTRA_GAME_TITLE = "com.tyranor.runtime.extra.GAME_TITLE";
    static final String EXTRA_PROTOCOL_VERSION = "com.tyranor.runtime.extra.PROTOCOL_VERSION";
    static final int PROTOCOL_VERSION = 1;

    private ExternalLaunchContract() {}
}
