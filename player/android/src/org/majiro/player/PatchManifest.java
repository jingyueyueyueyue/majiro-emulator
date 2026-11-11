package org.majiro.player;

import org.json.JSONArray;
import org.json.JSONObject;
import java.util.ArrayList;
import java.util.HashSet;
import java.util.Locale;

/** Data-only local resource layers. No title, game ID or executable is consulted. */
final class PatchManifest {
    static final class Layer {
        final String directory,kind,encoding;
        Layer(String directory,String kind){this(directory,kind,kind.equals("source_mjo")?"cp932":"cp932-tunnel");}
        Layer(String directory,String kind,String encoding){this.directory=directory;this.kind=kind;this.encoding=encoding;}
    }
    final ArrayList<Layer> layers=new ArrayList<Layer>();
    final ArrayList<String> archives=new ArrayList<String>();
    final ArrayList<String> localArchives=new ArrayList<String>();
    final ArrayList<String> imageArchives=new ArrayList<String>();
    final ArrayList<String> sharedImageArchives=new ArrayList<String>();
    String rawMetadata="",textPath="",textKind="",imageDirectory="",fontDirectory="patches/fonts",vmEncoding="cp932";
    boolean legacy,explicit;
    int schema=1;
    final java.util.TreeMap<String,String> archiveEncodings=new java.util.TreeMap<String,String>();
    final java.util.TreeMap<String,String> resourceHashes=new java.util.TreeMap<String,String>();
    final java.util.TreeMap<String,String> metadataHashes=new java.util.TreeMap<String,String>();

    static String path(String value) {
        if(value==null||value.length()==0||value.length()>1024||!value.startsWith("patches/")
                ||value.endsWith("/")||value.indexOf('\\')>=0||value.indexOf(':')>=0)
            throw new IllegalArgumentException("patch path must be relative under patches/");
        for(int i=0;i<value.length();++i)
            if(value.charAt(i)<32||value.charAt(i)==127)throw new IllegalArgumentException("patch path control byte");
        for(String part:value.split("/",-1))
            if(part.length()==0||part.equals(".")||part.equals(".."))
                throw new IllegalArgumentException("invalid patch path component");
        return value;
    }
    private static String optionalPath(JSONObject source,String key) {
        String value=source.optString(key,"");
        return value.isEmpty()?"":path(value);
    }
    static PatchManifest parse(String json,boolean local) throws Exception {
        PatchManifest result=new PatchManifest();
        if(json==null){
            if(!local)return result;
            result.legacy=true;
            result.layers.add(new Layer("patches/scenario","pinned_raw_patch"));
            result.layers.add(new Layer("patches/local/scripts","source_mjo"));
            result.rawMetadata="patches/majiro_metadata.lua";
            result.textPath="patches/text_adapter.lua";result.textKind="sequential_text_v1";
            result.imageDirectory="patches/images";
            return result;
        }
        if(json.length()>65536)throw new IllegalArgumentException("patch manifest byte limit");
        JSONObject data=new JSONObject(json);
        for(String key:new String[]{"archives","image_archives","shared_image_archives","script_layers"})
            if(data.has(key)&&!(data.get(key) instanceof JSONArray))
                throw new IllegalArgumentException("patch manifest "+key+" must be an array");
        for(String key:new String[]{"archive_encodings","resource_hashes","metadata_hashes","text_adapter"})
            if(data.has(key)&&!(data.get(key) instanceof JSONObject))
                throw new IllegalArgumentException("patch manifest "+key+" must be an object");
        Object schema=data.get("schema");
        if(!(schema instanceof Number)||(((Number)schema).doubleValue()!=1&&((Number)schema).doubleValue()!=2))
            throw new IllegalArgumentException("unsupported patch manifest schema");
        result.schema=((Number)schema).intValue();
        result.explicit=true;
        JSONArray archives=data.optJSONArray("archives");
        HashSet<String> archiveNames=new HashSet<String>();
        if(archives!=null){
            if(archives.length()>128)throw new IllegalArgumentException("patch archive count");
            for(int i=0;i<archives.length();++i){
                String archive=path(archives.getString(i));
                if(!archive.toLowerCase(Locale.ROOT).endsWith(".arc")
                        ||!archiveNames.add(archive.toLowerCase(Locale.ROOT)))
                    throw new IllegalArgumentException("invalid or duplicate patch archive");
                result.localArchives.add(archive);
            }
        }
        JSONArray images=data.optJSONArray("image_archives");
        HashSet<String> imageNames=new HashSet<String>();
        if(images!=null){
            if(images.length()>128)throw new IllegalArgumentException("image archive count");
            for(int i=0;i<images.length();++i){
                String archive=path(images.getString(i));
                if(!archive.toLowerCase(Locale.ROOT).endsWith(".arc")||
                    !imageNames.add(archive.toLowerCase(Locale.ROOT)))
                    throw new IllegalArgumentException("invalid or duplicate image archive");
                result.imageArchives.add(archive);
            }
        }
        if(result.schema==2){
            JSONArray shared=data.optJSONArray("shared_image_archives");
            if(shared!=null)for(int i=0;i<shared.length();++i){
                String name=path(shared.getString(i));
                if(!result.imageArchives.contains(name)||result.sharedImageArchives.contains(name))
                    throw new IllegalArgumentException("shared image archive must have one image-only declaration");
                result.sharedImageArchives.add(name);
            }
            JSONObject encodings=data.optJSONObject("archive_encodings");
            if(encodings!=null)for(java.util.Iterator<String> keys=encodings.keys();keys.hasNext();){
                String name=keys.next();
                String archive=path(name);
                if(!result.localArchives.contains(archive)||result.imageArchives.contains(archive))
                    throw new IllegalArgumentException("encoding for undeclared or image-only archive");
                result.archiveEncodings.put(archive,encoding(encodings.getString(name)));
            }
            hashes(data.optJSONObject("resource_hashes"),result.resourceHashes,false);
            hashes(data.optJSONObject("metadata_hashes"),result.metadataHashes,true);
        }
        // Existing schema 1 declares shared image resources. Keep their original
        // cross-mode visibility while native image scope excludes scripts/text.
        if(result.schema==1)result.sharedImageArchives.addAll(result.imageArchives);
        // Image scope wins even when an old manifest also lists the same ARC
        // under archives. This prevents CP936 MJO in image updates leaking in.
        if(!local)return result;
        JSONArray layers=data.optJSONArray("script_layers");
        HashSet<String> seen=new HashSet<String>();boolean pinned=false;
        if(layers!=null){
            if(layers.length()>32)throw new IllegalArgumentException("patch layer count");
            for(int i=0;i<layers.length();++i){
                JSONObject layer=layers.getJSONObject(i);
                String directory=path(layer.getString("directory")),kind=layer.getString("kind");
                if(!kind.equals("pinned_raw_patch")&&!kind.equals("source_mjo")&&
                    !(result.schema==2&&kind.equals("encoded_text")))
                    throw new IllegalArgumentException("unsupported patch script kind");
                if(!seen.add(directory.toLowerCase(Locale.ROOT)))throw new IllegalArgumentException("duplicate patch layer");
                String codec=kind.equals("encoded_text")?encoding(layer.getString("encoding")):
                    kind.equals("source_mjo")?"cp932":"cp932-tunnel";
                if(layer.has("encoding")&&!codec.equals(layer.getString("encoding")))
                    throw new IllegalArgumentException("script kind and encoding disagree");
                result.layers.add(new Layer(directory,kind,codec));pinned|=kind.equals("pinned_raw_patch");
            }
        }
        result.archives.addAll(result.localArchives);
        result.rawMetadata=optionalPath(data,"raw_metadata");
        if(pinned&&result.rawMetadata.isEmpty())throw new IllegalArgumentException("pinned patch metadata is missing");
        JSONObject text=data.optJSONObject("text_adapter");
        if(text!=null){
            result.textKind=text.getString("kind");
            if(!result.textKind.equals("sequential_text_v1"))
                throw new IllegalArgumentException("unsupported patch text format");
            result.textPath=path(text.getString("path"));
        }
        boolean source=false,encoded=!result.archiveEncodings.isEmpty();
        for(Layer layer:result.layers){
            source|=layer.kind.equals("source_mjo");encoded|=layer.kind.equals("encoded_text");
        }
        if((source&&result.textPath.isEmpty())||(!result.textPath.isEmpty()&&!source))
            throw new IllegalArgumentException("source_mjo and sequential adapter must be declared together");
        if((pinned||!result.rawMetadata.isEmpty())&&(source||encoded||!result.textPath.isEmpty()))
            throw new IllegalArgumentException("conflicting text adapters");
        if(encoded&&(source||!result.textPath.isEmpty()||result.resourceHashes.isEmpty()))
            throw new IllegalArgumentException("encoded resources require hashes and one coherent codec strategy");
        if(encoded)result.vmEncoding=encoding(data.getString("vm_encoding"));
        result.imageDirectory=optionalPath(data,"image_directory");
        String font=optionalPath(data,"font_directory");
        if(!font.isEmpty())result.fontDirectory=font;
        return result;
    }
    String[] layerRows(){
        String[] result=new String[layers.size()];
        for(int i=0;i<result.length;++i)result[i]=layers.get(i).kind+"\t"+layers.get(i).directory+"\t"+layers.get(i).encoding;
        return result;
    }
    private static String encoding(String value){
        if(!value.equals("cp932")&&!value.equals("cp936")&&!value.equals("utf-8"))
            throw new IllegalArgumentException("unsupported declared text encoding");
        return value;
    }
    private static void hashes(JSONObject input,java.util.Map<String,String> result,boolean metadata)throws Exception{
        if(input==null)return;
        if(input.length()>10000)throw new IllegalArgumentException("text hash inventory limit");
        for(java.util.Iterator<String> keys=input.keys();keys.hasNext();){
            String name=keys.next();
            if(metadata)path(name);
            else if(!name.equals(name.toLowerCase(Locale.ROOT))||name.indexOf('/')>=0||
                name.indexOf('\\')>=0||name.indexOf(':')>=0||!name.matches("[^\\p{Cntrl}]+\\.(mjo|cfg|env|txt)"))
                throw new IllegalArgumentException("invalid text resource hash key");
            String hash=input.getString(name);
            if(!hash.matches("[0-9a-f]{64}"))throw new IllegalArgumentException("invalid text resource digest");
            result.put(name,hash);
        }
    }
    boolean hasText(){
        return !rawMetadata.isEmpty()||!textPath.isEmpty()||!layers.isEmpty()||!archiveEncodings.isEmpty();
    }
    void validateIsolation(TextMode.Resources resources)throws Exception{
        java.util.HashSet<String> declared=new java.util.HashSet<String>(localArchives);
        declared.addAll(imageArchives);
        for(String name:declared)if(!resources.file(name))
            throw new IllegalArgumentException("local archive missing; cannot verify base/patch separation: "+name);
    }
    String archiveScope(String path,boolean local){
        path=path.toLowerCase(Locale.ROOT);
        for(String name:imageArchives)if(name.equalsIgnoreCase(path))
            return local||sharedImageArchives.contains(name)?"images":"blocked";
        for(String name:localArchives)if(name.equalsIgnoreCase(path)){
            if(!local)return "blocked";
            return archiveEncodings.containsKey(name)?archiveEncodings.get(name):"untyped_local";
        }
        return path.toLowerCase(Locale.ROOT).startsWith("patches/")?"blocked":"original";
    }
    String configuration() throws Exception {
        JSONArray declarations=new JSONArray();
        for(Layer layer:layers)declarations.put(new JSONObject().put("directory",layer.directory)
            .put("kind",layer.kind).put("encoding",layer.encoding));
        return new JSONObject().put("schema",schema).put("legacy",legacy).put("raw_metadata",rawMetadata)
            .put("text_adapter",new JSONObject().put("kind",textKind).put("path",textPath))
            .put("script_layers",declarations).put("resource_hashes",new JSONObject(resourceHashes))
            .put("metadata_hashes",new JSONObject(metadataHashes))
            .put("vm_encoding",vmEncoding)
            .put("image_directory",imageDirectory).put("font_directory",fontDirectory).toString();
    }
}
