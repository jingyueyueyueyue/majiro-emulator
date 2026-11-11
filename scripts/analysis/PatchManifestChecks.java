package org.majiro.player;
import org.json.JSONArray;
import org.json.JSONObject;
/** Production data parser checks without Android IO or game execution. */
public final class PatchManifestChecks {
    static int checks;
    static void check(boolean value,String why){++checks;if(!value)throw new AssertionError(why);}
    static void bad(String json) throws Exception {
        boolean rejected=false;
        try{PatchManifest.parse(json,true);}catch(Exception error){rejected=true;}
        check(rejected,"invalid manifest accepted: "+json);
    }
    public static void main(String[] args)throws Exception {
        JSONObject source=new JSONObject().put("schema",1)
            .put("script_layers",new JSONArray()
                .put(new JSONObject().put("directory","patches/other/source").put("kind","source_mjo")))
            .put("archives",new JSONArray().put("patches/a/20.arc").put("patches/z/99.arc"))
            .put("image_archives",new JSONArray().put("patches/z/99.arc").put("patches/a/20.arc"))
            .put("text_adapter",new JSONObject().put("kind","sequential_text_v1").put("path","patches/other/data.lua"))
            .put("image_directory","patches/custom/images").put("font_directory","patches/common/fonts");
        PatchManifest parsed=PatchManifest.parse(source.toString(),true);
        check(!parsed.legacy&&parsed.layers.size()==1,"explicit manifest");
        check(parsed.explicit&&parsed.localArchives.equals(parsed.archives),"local archive declarations retained");
        check(parsed.layerRows()[0].equals("source_mjo\tpatches/other/source\tcp932"),"explicit source encoding");
        check(parsed.archives.get(0).equals("patches/a/20.arc")&&parsed.archives.get(1).equals("patches/z/99.arc"),
            "archive order is declared priority, independent of lexicographic path");
        JSONObject conflict=new JSONObject(source.toString()).put("raw_metadata","patches/任意/metadata.lua");
        bad(conflict.toString());
        check(parsed.textKind.equals("sequential_text_v1")&&parsed.textPath.equals("patches/other/data.lua"),
            "text adapter dispatch is by format");
        JSONObject config=new JSONObject(parsed.configuration());
        check(config.getJSONObject("text_adapter").getString("path").equals(parsed.textPath),"boot contract");
        check(config.getString("image_directory").equals("patches/custom/images"),"image directory mapping");
        check(config.getString("font_directory").equals("patches/common/fonts"),"font directory mapping");
        for(String value:new String[]{source.toString(),null}){
            PatchManifest original=PatchManifest.parse(value,false);
            check(original.layers.isEmpty()&&original.archives.isEmpty()&&original.textPath.isEmpty()
                &&original.rawMetadata.isEmpty()&&!original.legacy,"non-local does not mount script or text translation");
            check(original.imageArchives.size()==(value==null?0:2),"declared images independent of text mode");
            check(original.localArchives.size()==(value==null?0:2),
                "non-local retains declarations for duplicate detection without mounting scripts");
        }
        check(PatchManifest.parse(source.toString(),false).imageArchives.get(0).equals("patches/z/99.arc"),
            "image archive priority preserved");
        PatchManifest legacy=PatchManifest.parse(null,true);
        check(legacy.legacy&&legacy.layers.size()==2&&legacy.textPath.equals("patches/text_adapter.lua"),"legacy generic fallback");
        PatchManifest empty=PatchManifest.parse("{\"schema\":1}",true);
        check(!empty.legacy&&empty.layers.isEmpty()&&empty.archives.isEmpty(),"explicit empty does not enable legacy");
        for(String path:new String[]{"../x","/patches/x","C:/x","patches","patches/","patches/../x","patches/a/../../x",
                "patches/./x","patches//x","patches/a\\x","patches/x:y","patches/x\nz","patches/x\tz","patches/x\0z","other/x"}){
            boolean rejected=false;try{PatchManifest.path(path);}catch(IllegalArgumentException expected){rejected=true;}
            check(rejected,"unsafe patch path accepted");
        }
        check(PatchManifest.parse("{\"schema\":2}",true).schema==2,"explicit encoding schema");
        bad("{\"schema\":3}");bad("{\"schema\":1.5}");bad("{\"schema\":\"1\"}");
        bad("{\"schema\":1,\"script_layers\":[{\"directory\":\"patches/x\",\"kind\":\"exe\"}]}");
        bad("{\"schema\":1,\"script_layers\":[{\"directory\":\"patches/x\",\"kind\":\"pinned_raw_patch\"}]}");
        bad("{\"schema\":1,\"archives\":[\"patches/a.exe\"]}");
        bad("{\"schema\":1,\"archives\":[\"patches/A.arc\",\"patches/a.arc\"]}");
        bad("{\"schema\":1,\"image_archives\":[\"patches/a.exe\"]}");
        bad("{\"schema\":1,\"image_archives\":[\"patches/A.arc\",\"patches/a.arc\"]}");
        bad("{\"schema\":1,\"text_adapter\":{\"kind\":\"native_dll\",\"path\":\"patches/a.lua\"}}");
        JSONObject duplicate=new JSONObject(source.toString());
        duplicate.getJSONArray("script_layers").put(new JSONObject().put("directory","patches/OTHER/source").put("kind","source_mjo"));
        bad(duplicate.toString());
        JSONObject tooMany=new JSONObject().put("schema",1);JSONArray list=new JSONArray();
        for(int i=0;i<129;i++)list.put("patches/x/"+i+".arc");tooMany.put("archives",list);bad(tooMany.toString());
        list=new JSONArray();for(int i=0;i<33;i++)list.put(new JSONObject().put("directory","patches/x"+i).put("kind","source_mjo"));
        tooMany=new JSONObject().put("schema",1).put("script_layers",list);bad(tooMany.toString());
        bad(new String(new char[65537]).replace('\0',' '));
        System.out.println("{\"status\":\"passed\",\"checks\":"+checks+"}");
    }
}
