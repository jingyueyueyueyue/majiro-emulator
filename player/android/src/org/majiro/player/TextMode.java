package org.majiro.player;

/** User preference is separate from the effective resource mode for each game. */
final class TextMode {
    interface Resources {
        boolean file(String path) throws Exception;
        boolean populated(String directory) throws Exception;
    }
    static boolean valid(String mode){
        return "auto".equals(mode)||"original".equals(mode)||"local".equals(mode)||"online".equals(mode);
    }
    static String effective(String requested,boolean localPatch){
        if(!valid(requested))throw new IllegalArgumentException("unknown text mode");
        return "auto".equals(requested)?localPatch?"local":"original":requested;
    }
    static String resolve(String requested,PatchManifest patch,Resources resources) throws Exception {
        if(!"auto".equals(requested))return effective(requested,false);
        boolean available=!patch.rawMetadata.isEmpty()&&resources.file(patch.rawMetadata)
            ||!patch.textPath.isEmpty()&&resources.file(patch.textPath);
        for(String archive:patch.archives)available|=resources.file(archive);
        for(PatchManifest.Layer layer:patch.layers)available|=resources.populated(layer.directory);
        if(!patch.imageDirectory.isEmpty())available|=resources.populated(patch.imageDirectory);
        return effective(requested,available);
    }
    private TextMode(){}
}
