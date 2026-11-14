package org.majiro.player;

import java.io.File;
import java.io.FileInputStream;
import java.io.IOException;
import java.nio.file.Files;
import java.nio.file.StandardCopyOption;
import java.util.ArrayList;
import java.util.Arrays;
import org.json.JSONArray;
import org.json.JSONObject;

/** Read known save roots and copy immutable objects before publishing an index. */
final class SaveMigration {
    private final File current;
    private final ArrayList<File> roots=new ArrayList<File>();

    SaveMigration(File current,File companion) throws Exception {
        this.current=current.getCanonicalFile();
        roots.add(this.current);
        File other=companion.getCanonicalFile();
        if(!other.equals(this.current))roots.add(other);
    }

    interface ArchiveSizes { long size(String path) throws Exception; }
    static String legacyIdentity(String source,java.util.List<String> paths,
                                 java.util.Set<String> images,ArchiveSizes sizes) throws Exception {
        StringBuilder value=new StringBuilder(source);
        for(String name:paths){
            if(!name.endsWith(".arc")||name.equals("savedata.arc")||images.contains(name))continue;
            long bytes=sizes.size(name);
            if(bytes<0)throw new IOException("legacy archive missing: "+name);
            value.append(':').append(bytes);
        }
        byte[] hash=java.security.MessageDigest.getInstance("SHA-256")
            .digest(value.toString().getBytes(java.nio.charset.StandardCharsets.UTF_8));
        StringBuilder encoded=new StringBuilder();
        for(byte b:hash)encoded.append(String.format(java.util.Locale.ROOT,"%02x",b&255));
        return encoded.toString();
    }

    private File file(int root,String name) throws Exception {
        if(root<0||root>=roots.size()||!name.matches("[A-Za-z0-9_-][A-Za-z0-9_.-]{0,119}"))
            throw new IllegalArgumentException("invalid legacy save location");
        File base=roots.get(root),value=new File(base,name);
        if(!value.getCanonicalFile().getParentFile().equals(base))
            throw new IllegalArgumentException("legacy save escaped its root");
        return value;
    }

    String catalog() throws Exception {
        JSONArray result=new JSONArray();
        for(int root=0;root<roots.size();++root){
            File[] indexes=roots.get(root).listFiles(value->value.isFile()&&
                value.getName().matches("index(?:_[0-9a-f]{64})?\\.mjs"));
            if(indexes==null)continue;
            Arrays.sort(indexes,(a,b)->a.getName().compareTo(b.getName()));
            if(indexes.length>256)throw new IOException("too many legacy save indexes");
            for(File index:indexes)result.put(new JSONObject().put("root",root).put("name",index.getName()));
        }
        return result.toString();
    }

    byte[] read(int root,String name) throws Exception {
        File source=file(root,name);
        if(!source.isFile())return null;
        if(source.length()>32*1024*1024)throw new IOException("legacy save exceeds read limit");
        return Files.readAllBytes(source.toPath());
    }

    void copy(int root,String name) throws Exception {
        if(!name.matches("(?:[0-9a-f]{64}\\.mjb|thumb_[0-9a-f]{64}\\.png)"))
            throw new IllegalArgumentException("legacy copy requires an immutable save object");
        File source=file(root,name),destination=file(0,name);
        if(!source.isFile())throw new IOException("legacy save object missing: "+name);
        if(source.equals(destination))return;
        if(destination.exists()){
            try(FileInputStream left=new FileInputStream(source);FileInputStream right=new FileInputStream(destination)){
                if(!ArchiveLayerPolicy.sameContent(left,right))
                    throw new IOException("legacy save object collision: "+name);
            }
            return;
        }
        File pending=Files.createTempFile(current.toPath(),"save-import-",".writing").toFile();
        try{
            Files.copy(source.toPath(),pending.toPath(),StandardCopyOption.REPLACE_EXISTING);
            try(java.io.RandomAccessFile output=new java.io.RandomAccessFile(pending,"rw")){output.getFD().sync();}
            Files.move(pending.toPath(),destination.toPath(),StandardCopyOption.ATOMIC_MOVE);
        }finally{Files.deleteIfExists(pending.toPath());}
    }
}
