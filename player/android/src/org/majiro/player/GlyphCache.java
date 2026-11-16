package org.majiro.player;

import java.util.Iterator;
import java.util.LinkedHashMap;
import java.util.Map;

/** Retain complete masks within both a byte budget and an entry limit. */
final class GlyphCache {
    private static final class Key {
        final Object face;
        final String layout;
        Key(Object face,String layout){this.face=face;this.layout=layout;}
        @Override public int hashCode(){return 31*System.identityHashCode(face)+layout.hashCode();}
        @Override public boolean equals(Object other){
            if(!(other instanceof Key))return false;
            Key key=(Key)other;
            return face==key.face&&layout.equals(key.layout);
        }
    }

    private final LinkedHashMap<Key,int[]> entries=new LinkedHashMap<>(32,.75f,true);
    private final int entryLimit;
    private final long byteLimit;
    private long bytes;

    GlyphCache(int entryLimit,long byteLimit){
        if(entryLimit<=0||byteLimit<0)throw new IllegalArgumentException("glyph cache budget");
        this.entryLimit=entryLimit;this.byteLimit=byteLimit;
    }
    private static long cost(Key key,int[] pixels){
        return 4L*pixels.length+2L*key.layout.length();
    }
    synchronized int[] get(Object face,String layout){return entries.get(new Key(face,layout));}
    synchronized void put(Object face,String layout,int[] pixels){
        Key key=new Key(face,layout);
        int[] previous=entries.remove(key);
        if(previous!=null)bytes-=cost(key,previous);
        long size=cost(key,pixels);
        if(size>byteLimit)return;
        Iterator<Map.Entry<Key,int[]>> it=entries.entrySet().iterator();
        while(it.hasNext()&&(entries.size()>=entryLimit||bytes+size>byteLimit)){
            Map.Entry<Key,int[]> oldest=it.next();
            bytes-=cost(oldest.getKey(),oldest.getValue());it.remove();
        }
        entries.put(key,pixels);bytes+=size;
    }
    synchronized void clear(){entries.clear();bytes=0;}
    synchronized long bytes(){return bytes;}
    synchronized int size(){return entries.size();}
}
