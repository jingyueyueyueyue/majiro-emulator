package org.majiro.player;

import android.content.Context;
import android.graphics.Typeface;
import java.io.File;
import java.text.Normalizer;
import java.util.HashMap;
import java.util.Locale;

/** Optional user-owned Windows faces; never bundled in the public APK. */
final class OriginalTypefaces {
    private static final HashMap<Integer,Typeface> loaded=new HashMap<>();
    private static boolean inspected;
    private static File collection;

    static int gothicIndex(String requested){
        String name=Normalizer.normalize(requested,Normalizer.Form.NFKC)
            .replace(" ","").replace("-","").toLowerCase(Locale.ROOT);
        if(name.equals("msgothic")||name.equals("msゴシック"))return 0;
        if(name.equals("msuigothic"))return 1;
        if(name.equals("mspgothic")||name.equals("mspゴシック"))return 2;
        return -1;
    }
    static synchronized Typeface resolve(Context context,String requested){
        int index=gothicIndex(requested);
        if(index<0)return null;
        if(!inspected){
            collection=new File(context.getFilesDir(),"fonts/msgothic.ttc");
            if(!collection.isFile())collection=null;
            inspected=true;
        }
        if(collection==null)return null;
        Typeface face=loaded.get(index);
        if(face==null){
            face=new Typeface.Builder(collection).setTtcIndex(index).build();
            loaded.put(index,face);
        }
        return face;
    }
    static synchronized void reset(){loaded.clear();inspected=false;collection=null;}
    private OriginalTypefaces(){}
}
