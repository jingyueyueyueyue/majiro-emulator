package org.majiro.player;

import java.nio.charset.StandardCharsets;
import java.util.ArrayList;

/** Optional engine version/API hints. No executable hash or allowlist. */
public final class EngineFeatures {
    private EngineFeatures(){}
    private static boolean contains(byte[] source,byte[] value){
        outer:for(int at=0;at<=source.length-value.length;++at){
            for(int i=0;i<value.length;++i)if(source[at+i]!=value[i])continue outer;
            return true;
        }
        return false;
    }
    public static String[] from(byte[] source){
        ArrayList<String> features=new ArrayList<String>();
        if(!contains(source,"MajiroObj".getBytes(StandardCharsets.US_ASCII)))return new String[0];
        for(int version=102;version<=105;++version)
            if(contains(source,("MajiroSavV1."+version).getBytes(StandardCharsets.US_ASCII)))
                features.add("save:"+version);
        // Hashes here are the engine's public syscall identifiers, not EXE
        // fingerprints. Match the direct CMP dispatch encoding.
        int[] identifiers={0x2bd1709d,0x7e0b8320,0x87a631a6,
            0x00000000};
        for(int value:identifiers){
            if(value==0)continue;
            for(int at=0;at+6<=source.length;++at){
                int start=-1;
                if((source[at]&255)==0x3d)start=at+1;
                else if((source[at]&255)==0x81&&(source[at+1]&255)>=0xf8)start=at+2;
                if(start<0)continue;
                int actual=(source[start]&255)|(source[start+1]&255)<<8|
                    (source[start+2]&255)<<16|(source[start+3]&255)<<24;
                if(actual==value){features.add("api:"+Integer.toHexString(value));break;}
            }
        }
        return features.toArray(new String[features.size()]);
    }
}
