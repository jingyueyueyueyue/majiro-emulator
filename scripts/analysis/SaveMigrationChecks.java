package org.majiro.player;

import java.io.*;
import java.nio.file.*;
import org.json.*;

public final class SaveMigrationChecks {
    static int checks;
    static void check(boolean value,String why){checks++;if(!value)throw new AssertionError(why);}
    static void rejected(Action action)throws Exception{
        try{action.run();throw new AssertionError("invalid operation accepted");}
        catch(IOException|IllegalArgumentException expected){checks++;}
    }
    interface Action{void run()throws Exception;}
    public static void main(String[] args)throws Exception{
        Path base=Paths.get(args[0]),current=base.resolve("original"),legacy=base;
        Files.createDirectories(current);
        String hash=String.join("",java.util.Collections.nCopies(64,"a"));
        java.util.List<String> order=java.util.Arrays.asList("patches/local.arc","patches/images.arc",
            "update.arc","scenario.arc","savedata.arc","bin");
        java.util.Set<String> images=new java.util.HashSet<String>();
        images.add("patches/images.arc");
        java.util.Map<String,Long> sizes=new java.util.HashMap<String,Long>();
        sizes.put("patches/local.arc",11L);sizes.put("update.arc",11L);sizes.put("scenario.arc",22L);
        String identity=SaveMigration.legacyIdentity("same-game",order,images,name->sizes.get(name));
        byte[] expected=java.security.MessageDigest.getInstance("SHA-256").digest("same-game:11:11:22".getBytes("UTF-8"));
        StringBuilder expectedHex=new StringBuilder();
        for(byte b:expected)expectedHex.append(String.format(java.util.Locale.ROOT,"%02x",b&255));
        check(identity.equals(expectedHex.toString()),"0.1 archive order/duplicate/image exclusion changed");
        Files.write(legacy.resolve("index.mjs"),"old index".getBytes("UTF-8"));
        Files.write(legacy.resolve("thumb_"+hash+".png"),new byte[]{1,2,3});
        Files.write(legacy.resolve(hash+".mjb"),new byte[]{4,5});
        SaveMigration migration=new SaveMigration(current.toFile(),legacy.toFile());
        JSONArray catalog=new JSONArray(migration.catalog());
        check(catalog.length()==1&&catalog.getJSONObject(0).getInt("root")==1,"companion index not discovered");
        check(new String(migration.read(1,"index.mjs"),"UTF-8").equals("old index"),"old index read");
        migration.copy(1,"thumb_"+hash+".png");migration.copy(1,"thumb_"+hash+".png");
        check(java.util.Arrays.equals(Files.readAllBytes(current.resolve("thumb_"+hash+".png")),
            new byte[]{1,2,3}),"thumbnail copy/idempotence");
        migration.copy(1,hash+".mjb");
        check(java.util.Arrays.equals(Files.readAllBytes(current.resolve(hash+".mjb")),new byte[]{4,5}),"object copy");
        Files.write(current.resolve(hash+".mjb"),new byte[]{9});
        rejected(()->migration.copy(1,hash+".mjb"));
        check(Files.readAllBytes(current.resolve(hash+".mjb"))[0]==9,"collision overwrote destination");
        rejected(()->migration.copy(1,"index.mjs"));
        rejected(()->migration.read(1,"../index.mjs"));
        rejected(()->migration.read(-1,"index.mjs"));
        rejected(()->migration.read(2,"index.mjs"));
        try(java.util.stream.Stream<Path> entries=Files.list(current)){
            check(entries.noneMatch(p->p.toString().endsWith(".writing")),"temporary copy leaked");
        }
        check(new String(Files.readAllBytes(legacy.resolve("index.mjs")),"UTF-8").equals("old index"),
            "legacy index modified");
        System.out.println("{\"status\":\"passed\",\"checks\":"+checks+"}");
    }
}
