package org.majiro.player;

import java.io.InputStream;
import java.util.ArrayList;
import java.util.HashMap;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Locale;

/** Keep copies of declared local patches out of the base resource layer. */
final class ArchiveLayerPolicy {
    interface Resources {
        long size(String path) throws Exception;
        InputStream open(String path) throws Exception;
    }

    static LinkedHashMap<String,String> duplicates(List<String> base,List<String> local,
                                                    Resources resources) throws Exception {
        HashMap<Long,ArrayList<String>> candidates=new HashMap<Long,ArrayList<String>>();
        for(String path:local){
            long size=resources.size(path);
            if(size<=0)continue;
            ArrayList<String> paths=candidates.get(size);
            if(paths==null){paths=new ArrayList<String>();candidates.put(size,paths);}
            if(!paths.contains(path))paths.add(path);
        }
        LinkedHashMap<String,String> result=new LinkedHashMap<String,String>();
        if(candidates.isEmpty())return result;
        for(String path:base){
            String name=path.toLowerCase(Locale.ROOT);
            if(!name.endsWith(".arc")||name.equals("savedata.arc"))continue;
            ArrayList<String> paths=candidates.get(resources.size(path));
            if(paths==null)continue;
            for(String candidate:paths){
                try(InputStream left=resources.open(path);InputStream right=resources.open(candidate)){
                    if(left==null||right==null)throw new IllegalStateException("cannot read archive layer");
                    if(sameContent(left,right)){result.put(path,candidate);break;}
                }
            }
        }
        return result;
    }

    static boolean sameContent(InputStream left,InputStream right) throws Exception {
        byte[] a=new byte[65536],b=new byte[65536];
        while(true){
            int count=block(left,a),other=block(right,b);
            if(count!=other)return false;
            if(count==0)return true;
            for(int i=0;i<count;++i)if(a[i]!=b[i])return false;
        }
    }
    private static int block(InputStream input,byte[] buffer) throws Exception {
        int count=0;
        while(count<buffer.length){
            int read=input.read(buffer,count,buffer.length-count);
            if(read<0)break;
            if(read==0){
                int value=input.read();
                if(value<0)break;
                buffer[count++]=(byte)value;
            }else count+=read;
        }
        return count;
    }
    private ArchiveLayerPolicy(){}
}
