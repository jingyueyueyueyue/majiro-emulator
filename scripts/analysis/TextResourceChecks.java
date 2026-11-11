package org.majiro.player;
import org.json.*;
import java.io.*;
import java.nio.file.*;
import java.nio.charset.*;
import java.util.*;

/** Plans use the production manifest, selector and duplicate isolation. */
public final class TextResourceChecks {
    static int checks;
    static void check(boolean value,String label){checks++;if(!value)throw new AssertionError(label);}
    static void rejects(Action action,String label)throws Exception{
        boolean failed=false;try{action.run();}catch(Exception expected){failed=true;}check(failed,label);
    }
    interface Action{void run()throws Exception;}
    static TextMode.Resources resources(final Path root){
        return new TextMode.Resources(){
            public boolean file(String name){return Files.isRegularFile(root.resolve(name));}
            public boolean populated(String name)throws Exception{
                Path path=root.resolve(name);if(!Files.isDirectory(path))return false;
                try(java.util.stream.Stream<Path> files=Files.list(path)){return files.anyMatch(Files::isRegularFile);}
            }
        };
    }
    static String decode(byte[] raw,String codec)throws Exception{
        return Charset.forName(codec).newDecoder().onMalformedInput(CodingErrorAction.REPORT)
            .onUnmappableCharacter(CodingErrorAction.REPORT).decode(java.nio.ByteBuffer.wrap(raw)).toString();
    }
    public static void main(String[] args)throws Exception{
        byte[] ambiguous={(byte)0xd6,(byte)0xd0};
        check(!decode(ambiguous,"windows-31j").equals(decode(ambiguous,"GBK")),"valid bytes have two meanings");
        rejects(()->decode(new byte[]{(byte)0x81},"windows-31j"),"strict truncated CP932");
        rejects(()->decode(new byte[]{(byte)0xff},"UTF-8"),"strict UTF8");
        String sequential="{\"schema\":1,\"script_layers\":[{\"kind\":\"source_mjo\",\"directory\":\"patches/s\"}],"+
            "\"text_adapter\":{\"kind\":\"sequential_text_v1\",\"path\":\"patches/a.lua\"}}";
        PatchManifest patch=PatchManifest.parse(sequential,true);
        Set<String> files=new HashSet<>(),dirs=new HashSet<>();
        TextMode.Resources memory=new TextMode.Resources(){
            public boolean file(String p){return files.contains(p);}
            public boolean populated(String p){return dirs.contains(p);}
        };
        rejects(()->TextMode.resolve("auto",patch,memory),"declared missing layer must not fall back");
        files.add("patches/a.lua");
        rejects(()->TextMode.resolve("auto",patch,memory),"adapter alone is incomplete");
        dirs.add("patches/s");
        check(TextMode.resolve("auto",patch,memory).equals("local"),"coherent local first");
        check(TextMode.resolve("original",patch,memory).equals("original"),"explicit original");
        check(TextMode.resolve("online",patch,memory).equals("online"),"explicit online");
        check(TextMode.resolve("local",PatchManifest.parse("{\"schema\":1}",true),memory).equals("original"),
            "existing local preference falls back to original when no local text layer is declared");
        check(TextMode.resolve("auto",PatchManifest.parse("{\"schema\":1}",true),memory).equals("original"),"no patch");
        rejects(()->PatchManifest.parse("{\"schema\":1,\"script_layers\":[{\"directory\":\"patches/x\",\"kind\":\"source_mjo\"}]}",true),
            "unpaired source layer");
        String both="{\"schema\":1,\"archives\":[\"patches/u.arc\"],\"image_archives\":[\"patches/u.arc\"]}";
        check(PatchManifest.parse(both,true).archiveScope("patches/u.arc",true).equals("images"),"images win in local mode");
        check(PatchManifest.parse(both,false).archiveScope("patches/u.arc",false).equals("images"),"legacy shared images remain available in original and online");
        String explicitImages=both.replace("\"schema\":1","\"schema\":2");
        check(PatchManifest.parse(explicitImages,false).archiveScope("patches/u.arc",false).equals("blocked"),"schema 2 unshared images remain local");
        String sharedImages=explicitImages.replace("\"schema\":2","\"schema\":2,\"shared_image_archives\":[\"patches/u.arc\"]");
        check(PatchManifest.parse(sharedImages,false).archiveScope("patches/u.arc",false).equals("images"),"schema 2 explicitly shared images are available");
        rejects(()->PatchManifest.parse(both,false).validateIsolation(memory),"missing local ARC blocks ambiguous original separation");
        check(PatchManifest.parse(both,false).archiveScope("patches/other.arc",false).equals("blocked"),"dynamic patch bypass blocked");
        rejects(()->TextMode.resolve("auto",PatchManifest.parse("{\"schema\":1,\"archives\":[\"patches/a.arc\"]}",true),memory),
            "untyped archive cannot auto-enable text");
        String sha=String.join("",Collections.nCopies(64,"a"));
        String encoded="{\"schema\":2,\"vm_encoding\":\"cp936\",\"archives\":[\"patches/a.arc\"],\"archive_encodings\":{\"patches/a.arc\":\"cp936\"},"+
            "\"resource_hashes\":{\"start.mjo\":\""+sha+"\"}}";
        check(PatchManifest.parse(encoded,true).archiveScope("patches/a.arc",true).equals("cp936"),"explicit CP936 scope");
        rejects(()->PatchManifest.parse(encoded.replace("cp936","guess"),true),"no guessed encoding");
        rejects(()->PatchManifest.parse("{\"schema\":1,\"script_layers\":{}}",true),"bad layer type cannot become empty");
        rejects(()->PatchManifest.parse("{\"schema\":1,\"text_adapter\":[]}",true),"bad adapter type cannot become empty");
        check(!TranslationModePolicy.canApply("local","online"),"local switch requires restart");
        check(!TranslationModePolicy.canApply("auto","online"),"unresolved mode cannot switch");
        check(TranslationModePolicy.canApply("original","online")&&TranslationModePolicy.canApply("online","original"),
            "original and online share immutable resources");
        JSONArray games=new JSONArray();
        for(String arg:args){
            Path root=Paths.get(arg);String json=new String(Files.readAllBytes(root.resolve("patches/manifest.json")),StandardCharsets.UTF_8);
            PatchManifest full=PatchManifest.parse(json,true);
            full.validateIsolation(resources(root));
            String effective=TextMode.resolve("auto",full,resources(root));
            ArrayList<String> base=new ArrayList<>();
            try(DirectoryStream<Path> paths=Files.newDirectoryStream(root,"*.arc")){
                for(Path p:paths)if(!p.getFileName().toString().equals("savedata.arc"))base.add(p.getFileName().toString());
            }
            Collections.sort(base,Collections.reverseOrder());
            ArrayList<String> local=new ArrayList<>(full.localArchives);
            for(String p:full.imageArchives)if(!local.contains(p))local.add(p);
            Map<String,String> duplicates=ArchiveLayerPolicy.duplicates(base,local,new ArchiveLayerPolicy.Resources(){
                public long size(String p)throws Exception{return Files.exists(root.resolve(p))?Files.size(root.resolve(p)):-1;}
                public InputStream open(String p)throws Exception{return Files.newInputStream(root.resolve(p));}
            });
            JSONArray modes=new JSONArray();
            for(String mode:new String[]{"original","online","local"}){
                if(mode.equals("local")&&!effective.equals("local"))continue;
                PatchManifest selected=PatchManifest.parse(json,mode.equals("local"));
                ArrayList<String> paths=new ArrayList<>(selected.archives);
                for(String p:mode.equals("local")?selected.imageArchives:selected.sharedImageArchives)if(!paths.contains(p))paths.add(p);
                for(String p:base)if(!duplicates.containsKey(p))paths.add(p);
                JSONArray arcs=new JSONArray(),layers=new JSONArray();
                for(String p:paths)arcs.put(new JSONObject().put("path",p).put("scope",selected.archiveScope(p,mode.equals("local"))));
                for(PatchManifest.Layer layer:selected.layers)layers.put(new JSONObject().put("directory",layer.directory)
                    .put("kind",layer.kind).put("encoding",layer.encoding));
                modes.put(new JSONObject().put("mode",mode).put("archives",arcs).put("layers",layers)
                    .put("configuration",new JSONObject(selected.configuration())));
            }
            games.put(new JSONObject().put("root",root.toString()).put("auto",effective)
                .put("duplicates",new JSONObject(duplicates)).put("modes",modes));
        }
        System.out.println(new JSONObject().put("status","passed").put("checks",checks).put("games",games));
    }
}
