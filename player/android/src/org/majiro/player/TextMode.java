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
        if(!valid(requested))throw new IllegalArgumentException("unknown text mode");
        if("original".equals(requested)||"online".equals(requested))return requested;
        if(patch.legacy){
            boolean found=resources.file(patch.rawMetadata)||resources.file(patch.textPath)
                ||resources.populated("patches/local/archives");
            for(PatchManifest.Layer layer:patch.layers)found|=resources.populated(layer.directory);
            if(found)throw new IllegalArgumentException("local text resources require patches/manifest.json");
            return "original";
        }
        boolean available=patch.hasText();
        for(String archive:patch.archives)
            if(!patch.imageArchives.contains(archive)&&!patch.archiveEncodings.containsKey(archive))
                throw new IllegalArgumentException("local archive has no declared text encoding: "+archive);
        if(!available){
            return "original";
        }
        if(!patch.rawMetadata.isEmpty()&&!resources.file(patch.rawMetadata))
            throw new IllegalArgumentException("declared raw metadata missing");
        if(!patch.textPath.isEmpty()&&!resources.file(patch.textPath))
            throw new IllegalArgumentException("declared text adapter missing");
        for(String archive:patch.archives)if(!resources.file(archive))
            throw new IllegalArgumentException("declared local archive missing: "+archive);
        for(PatchManifest.Layer layer:patch.layers)if(!resources.populated(layer.directory))
            throw new IllegalArgumentException("declared local script layer missing or empty: "+layer.directory);
        return effective(requested,true);
    }
    private TextMode(){}
}
