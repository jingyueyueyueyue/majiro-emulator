package org.majiro.player;

/** Original and online modes share scripts; a local overlay requires a restart. */
final class TranslationModePolicy {
    static boolean canApply(String running,String requested){
        return running.equals(requested)||(!running.equals("local")&&
            (requested.equals("original")||requested.equals("online")));
    }
    private TranslationModePolicy(){}
}
