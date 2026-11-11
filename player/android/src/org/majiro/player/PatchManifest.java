package org.majiro.player;

import org.json.JSONArray;
import org.json.JSONObject;
import java.util.ArrayList;
import java.util.HashSet;
import java.util.Locale;

/** Data-only local resource layers. No title, game ID or executable is consulted. */
final class PatchManifest {
    static final class Layer {
        final String directory,kind;
        Layer(String directory,String kind){this.directory=directory;this.kind=kind;}
    }
    final ArrayList<Layer> layers=new ArrayList<Layer>();
    final ArrayList<String> archives=new ArrayList<String>();
    final ArrayList<String> imageArchives=new ArrayList<String>();
    String rawMetadata="",textPath="",textKind="",imageDirectory="",fontDirectory="patches/fonts";
    boolean legacy;

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
        Object schema=data.get("schema");
        if(!(schema instanceof Number)||((Number)schema).doubleValue()!=1)
            throw new IllegalArgumentException("unsupported patch manifest schema");
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
        // These declared archives expose images only. Script, environment and
        // text adapters remain exclusively controlled by local text mode.
        if(!local)return result;
        JSONArray layers=data.optJSONArray("script_layers");
        HashSet<String> seen=new HashSet<String>();boolean pinned=false;
        if(layers!=null){
            if(layers.length()>32)throw new IllegalArgumentException("patch layer count");
            for(int i=0;i<layers.length();++i){
                JSONObject layer=layers.getJSONObject(i);
                String directory=path(layer.getString("directory")),kind=layer.getString("kind");
                if(!kind.equals("pinned_raw_patch")&&!kind.equals("source_mjo"))
                    throw new IllegalArgumentException("unsupported patch script kind");
                if(!seen.add(directory.toLowerCase(Locale.ROOT)))throw new IllegalArgumentException("duplicate patch layer");
                result.layers.add(new Layer(directory,kind));pinned|=kind.equals("pinned_raw_patch");
            }
        }
        JSONArray archives=data.optJSONArray("archives");seen.clear();
        if(archives!=null){
            if(archives.length()>128)throw new IllegalArgumentException("patch archive count");
            for(int i=0;i<archives.length();++i){
                String archive=path(archives.getString(i));
                if(!archive.toLowerCase(Locale.ROOT).endsWith(".arc")
                        ||!seen.add(archive.toLowerCase(Locale.ROOT)))
                    throw new IllegalArgumentException("invalid or duplicate patch archive");
                result.archives.add(archive);
            }
        }
        result.rawMetadata=optionalPath(data,"raw_metadata");
        if(pinned&&result.rawMetadata.isEmpty())throw new IllegalArgumentException("pinned patch metadata is missing");
        JSONObject text=data.optJSONObject("text_adapter");
        if(text!=null){
            result.textKind=text.getString("kind");
            if(!result.textKind.equals("sequential_text_v1"))
                throw new IllegalArgumentException("unsupported patch text format");
            result.textPath=path(text.getString("path"));
        }
        result.imageDirectory=optionalPath(data,"image_directory");
        String font=optionalPath(data,"font_directory");
        if(!font.isEmpty())result.fontDirectory=font;
        return result;
    }
    String[] layerRows(){
        String[] result=new String[layers.size()];
        for(int i=0;i<result.length;++i)result[i]=layers.get(i).kind+"\t"+layers.get(i).directory;
        return result;
    }
    String configuration() throws Exception {
        return new JSONObject().put("schema",1).put("legacy",legacy).put("raw_metadata",rawMetadata)
            .put("text_adapter",new JSONObject().put("kind",textKind).put("path",textPath))
            .put("image_directory",imageDirectory).put("font_directory",fontDirectory).toString();
    }
}
