package org.majiro.player;

import java.io.ByteArrayInputStream;
import java.io.InputStream;
import java.nio.file.Files;
import java.nio.file.Path;
import java.nio.file.Paths;
import java.util.ArrayList;
import java.util.Arrays;
import java.util.HashMap;
import java.util.LinkedHashMap;
import java.util.List;
import org.json.JSONArray;
import org.json.JSONObject;

/** Exercise the production layer policy with streams and real deployed resources. */
public final class ArchiveLayerChecks {
    static int checks,opened;
    static void check(boolean value,String why){++checks;if(!value)throw new AssertionError(why);}
    static final class Memory implements ArchiveLayerPolicy.Resources {
        final HashMap<String,byte[]> files=new HashMap<String,byte[]>();
        public long size(String path){return files.containsKey(path)?files.get(path).length:-1;}
        public InputStream open(String path){
            ++opened;return new ByteArrayInputStream(files.get(path));
        }
    }
    static InputStream shortReads(byte[] bytes,final int limit){
        return new ByteArrayInputStream(bytes){
            @Override public synchronized int read(byte[] buffer,int offset,int count){
                return super.read(buffer,offset,Math.min(limit,count));
            }
        };
    }
    public static void main(String[] args)throws Exception {
        Memory memory=new Memory();
        memory.files.put("update.arc",new byte[]{1,2,3,4});
        memory.files.put("patches/local/archives/renamed.arc",new byte[]{1,2,3,4});
        memory.files.put("official.arc",new byte[]{1,2,3,5});
        memory.files.put("different-size.arc",new byte[]{1,2,3});
        memory.files.put("savedata.arc",new byte[]{1,2,3,4});
        List<String> local=Arrays.asList("patches/local/archives/renamed.arc");
        LinkedHashMap<String,String> found=ArchiveLayerPolicy.duplicates(
            Arrays.asList("official.arc","different-size.arc","update.arc","savedata.arc"),local,memory);
        check(found.size()==1&&found.get("update.arc").equals(local.get(0)),"match bytes independent of filename");
        check(!found.containsKey("official.arc"),"same filename or size cannot exclude distinct updates");
        check(!found.containsKey("savedata.arc"),"source saves are never inspected");
        int previous=opened;
        check(ArchiveLayerPolicy.duplicates(Arrays.asList("update.arc"),new ArrayList<String>(),memory).isEmpty()
            &&opened==previous,"no patch declaration means no content reads");
        check(ArchiveLayerPolicy.duplicates(Arrays.asList("update.arc"),Arrays.asList("missing.arc"),memory).isEmpty(),
            "absent optional local archive leaves base intact");
        byte[] large=new byte[196613];
        for(int i=0;i<large.length;++i)large[i]=(byte)(i*31);
        check(ArchiveLayerPolicy.sameContent(shortReads(large,7),shortReads(large,419)),
            "partial stream reads preserve equality across buffer boundaries");
        byte[] changed=large.clone();changed[changed.length-1]^=1;
        check(!ArchiveLayerPolicy.sameContent(shortReads(large,197),shortReads(changed,11)),
            "late byte difference cannot exclude an original archive");
        check(!ArchiveLayerPolicy.sameContent(new ByteArrayInputStream(large),
            new ByteArrayInputStream(Arrays.copyOf(large,large.length-1))),"different EOF is distinct");
        JSONObject report=new JSONObject().put("status","passed");
        JSONArray games=new JSONArray();
        for(String arg:args){
            final Path root=Paths.get(arg);
            Path manifest=root.resolve("patches/manifest.json");
            PatchManifest declaration=PatchManifest.parse(
                new String(Files.readAllBytes(manifest),java.nio.charset.StandardCharsets.UTF_8),false);
            ArrayList<String> bases=new ArrayList<String>();
            try(java.nio.file.DirectoryStream<Path> paths=Files.newDirectoryStream(root,"*.arc")){
                for(Path path:paths)bases.add(path.getFileName().toString());
            }
            ArrayList<String> candidates=new ArrayList<String>(declaration.localArchives);
            for(String path:declaration.imageArchives)if(!candidates.contains(path))candidates.add(path);
            ArchiveLayerPolicy.Resources files=new ArchiveLayerPolicy.Resources(){
                public long size(String path)throws Exception{
                    Path file=root.resolve(path);return Files.isRegularFile(file)?Files.size(file):-1;
                }
                public InputStream open(String path)throws Exception{return Files.newInputStream(root.resolve(path));}
            };
            LinkedHashMap<String,String> matches=ArchiveLayerPolicy.duplicates(bases,candidates,files);
            games.put(new JSONObject().put("directory",root.toString()).put("duplicates",new JSONObject(matches)));
            // Local and non-local manifests expose the same candidates, while
            // only local mode mounts their scripts.
            PatchManifest translated=PatchManifest.parse(
                new String(Files.readAllBytes(manifest),java.nio.charset.StandardCharsets.UTF_8),true);
            check(translated.localArchives.equals(declaration.localArchives),"same detection in every text mode");
            check(declaration.archives.isEmpty(),"original mode never mounts local script archives");
        }
        System.out.println(report.put("checks",checks).put("games",games));
    }
}
