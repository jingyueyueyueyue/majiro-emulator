package org.majiro.player;
import java.util.*;
public final class TextModeChecks {
    static int checks;
    static void check(boolean value,String why){++checks;if(!value)throw new AssertionError(why);}
    static final class Resources implements TextMode.Resources {
        final Set<String> files=new HashSet<>(),directories=new HashSet<>();
        public boolean file(String value){return files.contains(value);}
        public boolean populated(String value){return directories.contains(value);}
    }
    public static void main(String[]args)throws Exception {
        Resources resources=new Resources();
        PatchManifest empty=PatchManifest.parse("{\"schema\":1,\"font_directory\":\"patches/fonts\"}",true);
        check(TextMode.resolve("auto",empty,resources).equals("original"),"fonts alone are no translation");
        PatchManifest images=PatchManifest.parse("{\"schema\":1,\"image_archives\":[\"patches/images.arc\"]}",true);
        resources.files.add("patches/images.arc");
        check(TextMode.resolve("auto",images,resources).equals("original"),"image-only archives do not enable translated scripts");
        resources.files.clear();
        PatchManifest patch=PatchManifest.parse("{\"schema\":1,\"archives\":[\"patches/local/archives/update.arc\"],"+
            "\"script_layers\":[{\"directory\":\"patches/local/scripts\",\"kind\":\"source_mjo\"}],"+
            "\"text_adapter\":{\"kind\":\"sequential_text_v1\",\"path\":\"patches/text_adapter.lua\"}}",true);
        check(TextMode.resolve("auto",patch,resources).equals("original"),"absent source stays original");
        resources.files.add("patches/text_adapter.lua");
        check(TextMode.resolve("auto",patch,resources).equals("local"),"actual text adapter preferred");
        resources.files.clear();resources.files.add("patches/local/archives/update.arc");
        check(TextMode.resolve("auto",patch,resources).equals("local"),"resource translation preferred");
        resources.files.clear();resources.directories.add("patches/local/scripts");
        check(TextMode.resolve("auto",patch,resources).equals("local"),"loose script translation preferred");
        for(String choice:new String[]{"original","local","online"})
            check(TextMode.resolve(choice,patch,resources).equals(choice),"explicit mode retained");
        resources.directories.clear();
        check(TextMode.resolve("auto",PatchManifest.parse(null,true),resources).equals("original"),"empty legacy locations do not enable translation");
        resources.files.add("patches/majiro_metadata.lua");
        check(TextMode.resolve("auto",PatchManifest.parse(null,true),resources).equals("local"),"verified legacy metadata selected");
        check(TextMode.valid("auto")&&!TextMode.valid("unknown"),"preference validation");
        System.out.println("{\"status\":\"passed\",\"checks\":"+checks+"}");
    }
}
