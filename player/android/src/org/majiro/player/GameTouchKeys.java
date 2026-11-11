package org.majiro.player;

import java.util.concurrent.atomic.AtomicInteger;

/** One-shot original PC keys from the touchscreen drawer to the SDL thread. */
final class GameTouchKeys {
    private static final int[] KEYS={13,27,37,38,39,40,33,34};
    private static final AtomicInteger pending=new AtomicInteger();

    private GameTouchKeys(){}

    static void request(int key){
        for(int i=0;i<KEYS.length;i++){
            if(KEYS[i]==key){
                final int mask=1<<i;
                pending.getAndUpdate(value->value|mask);
                return;
            }
        }
        throw new IllegalArgumentException("Unsupported touchscreen game key: "+key);
    }

    static int consume(){return pending.getAndSet(0);}
    static void reset(){pending.set(0);}
}
