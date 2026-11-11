package org.majiro.player;

import java.util.concurrent.atomic.AtomicInteger;

/** Latest original window-menu command, consumed only by the SDL game thread. */
final class GameWindowCommands {
    private static final AtomicInteger pending=new AtomicInteger();
    private GameWindowCommands(){}
    static void request(int command){
        if(command!=1&&command!=3&&command!=99)
            throw new IllegalArgumentException("window menu command");
        pending.set(command);
    }
    static int consume(){return pending.getAndSet(0);}
    static void reset(){pending.set(0);}
}
