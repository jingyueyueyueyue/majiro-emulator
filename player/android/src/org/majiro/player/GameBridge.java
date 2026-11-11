package org.majiro.player;

import android.app.Activity;
import android.content.Context;
import android.content.ContentResolver;
import android.database.Cursor;
import android.graphics.Bitmap;
import android.graphics.BitmapFactory;
import android.graphics.Canvas;
import android.graphics.Color;
import android.graphics.Paint;
import android.graphics.Typeface;
import android.media.MediaMetadataRetriever;
import android.media.MediaPlayer;
import android.view.SurfaceHolder;
import android.view.SurfaceView;
import android.view.ViewGroup;
import android.widget.FrameLayout;
import android.app.AlertDialog;
import android.content.DialogInterface;
import android.net.Uri;
import android.os.ParcelFileDescriptor;
import android.provider.DocumentsContract;
import android.system.Os;
import android.system.OsConstants;
import java.io.ByteArrayOutputStream;
import java.io.File;
import java.io.FileInputStream;
import java.io.FileOutputStream;
import java.io.InputStream;
import java.nio.charset.Charset;
import java.nio.charset.StandardCharsets;
import java.security.MessageDigest;
import java.util.ArrayList;
import java.util.HashMap;
import java.util.Locale;
import java.util.concurrent.CountDownLatch;
import tv.danmaku.ijk.media.player.IjkMediaPlayer;
import tv.danmaku.ijk.media.player.IMediaPlayer;

/** Android I/O boundaries. No game logic is implemented in the Activity. */
public final class GameBridge {
    private static Context context;
    private static Uri tree;
    private static String rootId;
    private static String sourceIdentity;
    private static PatchManifest patchManifest;
    private static volatile String launchTextMode;
    private static final java.util.concurrent.atomic.AtomicInteger gameButtons=
        new java.util.concurrent.atomic.AtomicInteger();
    private static File gameFiles;
    private static volatile String mediaError;
    private static final HashMap<String, HashMap<String, String>> directories =
        new HashMap<String, HashMap<String, String>>();
    private static final HashMap<String,String> duplicateArchives = new HashMap<String,String>();
    private static final HashMap<String, MediaPlayer> players = new HashMap<String, MediaPlayer>();
    private static final HashMap<String, MediaPlayer> nextPlayers = new HashMap<String, MediaPlayer>();
    private static final HashMap<String, File> mediaFiles = new HashMap<String, File>();
    private static final HashMap<String, Boolean> audioCompletion = new HashMap<String, Boolean>();
    private static final AudioLifecycle<MediaPlayer> audioLifecycle =
        new AudioLifecycle<MediaPlayer>(players, new AudioLifecycle.Transport<MediaPlayer>() {
            @Override public boolean playing(MediaPlayer player) { return player.isPlaying(); }
            @Override public boolean pause(MediaPlayer player) {
                try {
                    if (!player.isPlaying()) return false;
                    player.pause(); return true;
                } catch (IllegalStateException error) {
                    try { if (!player.isPlaying()) return false; }
                    catch (IllegalStateException invalid) { /* Preserve the source failure below. */ }
                    throw error;
                }
            }
            @Override public void start(MediaPlayer player) { player.start(); }
        });
    private static IjkMediaPlayer video;
    private static boolean videoPaused;
    private static SurfaceView videoSurface;
    private static volatile int movieState;
    private static int movieVolume = 1000;
    private static Typeface customTypeface;
    private static volatile int fontQualityMode=1;
    private static final HashMap<String, Typeface> privateTypefaces = new HashMap<String, Typeface>();
    private static String fontNameFilter = "";
    private static android.content.ClipboardManager clipboard;
    private static volatile boolean clipboardChanged;
    private static android.content.ClipboardManager.OnPrimaryClipChangedListener clipboardListener;
    private static final ModalDialogState modalDialogs = new ModalDialogState();
    private static AlertDialog activeDialog;
    private static ModalDialogState.Request activeDialogRequest;

    public static void open(Activity activity, String uri) throws Exception {
        open(activity,uri,null);
    }
    public static void open(Activity activity,String uri,String documentRoot) throws Exception {
        DiagnosticLog.initialize(activity);
        context = activity;
        tree = Uri.parse(uri);
        String grantedRoot=DocumentsContract.getTreeDocumentId(tree);
        rootId=documentRoot==null||documentRoot.length()==0?grantedRoot:documentRoot;
        if(!rootId.equals(grantedRoot)) {
            boolean child=rootId.startsWith(grantedRoot+"/");
            if(!child)try {
                child=DocumentsContract.isChildDocument(activity.getContentResolver(),
                    DocumentsContract.buildDocumentUriUsingTree(tree,grantedRoot),
                    DocumentsContract.buildDocumentUriUsingTree(tree,rootId));
            }catch(UnsupportedOperationException ignored){}
            if(!child)throw new SecurityException("game root is outside the selected folder");
        }
        directories.clear();
        duplicateArchives.clear();
        gameButtons.set(0);
        GameWindowCommands.reset();
        GameTouchKeys.reset();
        patchManifest=null;launchTextMode=TranslationSettings.load(activity).mode;
        if((activity.getApplicationInfo().flags&android.content.pm.ApplicationInfo.FLAG_DEBUGGABLE)!=0) {
            String requestedMode=activity.getIntent().getStringExtra("diagnostic_mode");
            if("original".equals(requestedMode)||"local".equals(requestedMode))launchTextMode=requestedMode;
        }
        final String requestedTextMode=launchTextMode;
        if("auto".equals(launchTextMode)){
            PatchManifest candidate=patches();
            launchTextMode=TextMode.resolve(requestedTextMode,candidate,new TextMode.Resources(){
                @Override public boolean file(String path) throws Exception{return resolve(path)!=null;}
                @Override public boolean populated(String path) throws Exception {
                    Uri directory=resolve(path);
                    return directory!=null&&!children(DocumentsContract.getDocumentId(directory)).isEmpty();
                }
            });
            if(!"local".equals(launchTextMode))patchManifest=null;
        }
        mediaError = null;
        movieState = 0;
        customTypeface = null;
        privateTypefaces.clear();
        fontNameFilter = "";
        if(clipboard!=null&&clipboardListener!=null)clipboard.removePrimaryClipChangedListener(clipboardListener);
        clipboard=(android.content.ClipboardManager)activity.getSystemService(Context.CLIPBOARD_SERVICE);
        clipboardChanged=false;
        clipboardListener=new android.content.ClipboardManager.OnPrimaryClipChangedListener(){
            @Override public void onPrimaryClipChanged(){clipboardChanged=true;}
        };
        clipboard.addPrimaryClipChangedListener(clipboardListener);
        modalDialogs.reset();
        // Re-adding the same game through another parent grant keeps its saves.
        String stable=tree.getAuthority()+"\n"+rootId;
        android.content.SharedPreferences locations=context.getSharedPreferences("game-save-roots",0);
        String stableKey=hex(MessageDigest.getInstance("SHA-256").digest(stable.getBytes(StandardCharsets.UTF_8))).substring(0,24);
        String identity=locations.getString(stableKey,null);
        sourceIdentity=locations.getString(stableKey+"-source",stable);
        if(identity==null) {
            ArrayList<String> candidates=new ArrayList<String>();
            candidates.add(rootId.equals(grantedRoot)?uri:uri+"\n"+rootId);
            for(android.content.UriPermission grant:context.getContentResolver().getPersistedUriPermissions()) {
                Uri previous=grant.getUri();
                try {
                    if(tree.getAuthority().equals(previous.getAuthority())&&rootId.equals(DocumentsContract.getTreeDocumentId(previous)))
                        candidates.add(previous.toString());
                }catch(IllegalArgumentException ignored){}
            }
            identity=stableKey;
            for(String candidate:candidates) {
                String legacy=hex(MessageDigest.getInstance("SHA-256").digest(candidate.getBytes(StandardCharsets.UTF_8))).substring(0,24);
                if(new File(context.getFilesDir(),"games/"+legacy).isDirectory()){
                    identity=legacy;sourceIdentity=candidate;break;
                }
            }
            if(!locations.edit().putString(stableKey,identity).putString(stableKey+"-source",sourceIdentity).commit())
                throw new IllegalStateException("save identity");
        }
        String textMode=launchTextMode;
        // Existing development saves used the local translation. Original and
        // online text execute the same Japanese scripts and share their own root.
        gameFiles = new File(context.getFilesDir(), "games/" + identity+(textMode.equals("local")?"":"/original"));
        if (!gameFiles.isDirectory() && !gameFiles.mkdirs()) throw new IllegalStateException("save directory");
        DiagnosticLog.write("game","source="+tree+" root="+rootId+" storage="+identity+" requested="+requestedTextMode+" text="+textMode);
    }
    public static void debugLog(String message){DiagnosticLog.write("runtime",message);}
    public static String sourceIdentity(){return sourceIdentity;}
    public static String translationMode(){return launchTextMode!=null?launchTextMode:TranslationSettings.load(context).mode;}
    public static void requestOriginalMenu(){gameButtons.getAndUpdate(value->value|2);}
    public static int consumeGameButtons(){return gameButtons.getAndSet(0);}
    public static void requestGameKey(int key){GameTouchKeys.request(key);}
    public static int consumeGameKeys(){return GameTouchKeys.consume();}
    public static void requestWindowMenu(int command){
        GameWindowCommands.request(command);
    }
    public static int consumeWindowMenu(){return GameWindowCommands.consume();}
    public static void reportWindowMenuResult(String result){
        if("queued".equals(result))return;
        Context current=context;
        if(current instanceof Activity)((Activity)current).runOnUiThread(()->{
            if(!((Activity)current).isFinishing())
                android.widget.Toast.makeText(current,"当前页面暂时无法打开这个游戏菜单",
                    android.widget.Toast.LENGTH_SHORT).show();
        });
    }
    public static boolean applyTextMode(String requested){
        if(!TranslationModePolicy.canApply(translationMode(),requested))return false;
        launchTextMode=requested;
        TranslationService.call(context,"clear","");
        DiagnosticLog.write("translation","live text mode="+requested);
        return true;
    }
    public static String translation(String action,String payload){return TranslationService.call(context,action,payload);}
    public static void cancelPendingMedia(){MovieTextures.stop();}
    private static PatchManifest patches() throws Exception {
        if(patchManifest!=null)return patchManifest;
        String text=null;
        final boolean inspectLocal="local".equals(launchTextMode)||"auto".equals(launchTextMode);
        {
            Uri uri=resolve("patches/manifest.json");
            if(uri!=null)try(InputStream input=context.getContentResolver().openInputStream(uri)){
                if(input==null)throw new IllegalStateException("cannot read patch manifest");
                ByteArrayOutputStream out=new ByteArrayOutputStream();byte[] block=new byte[4096];int count;
                while((count=input.read(block))!=-1){
                    if(out.size()+count>65536)throw new IllegalArgumentException("patch manifest byte limit");
                    out.write(block,0,count);
                }
                text=new String(out.toByteArray(),StandardCharsets.UTF_8);
            }
        }
        patchManifest=PatchManifest.parse(text,inspectLocal);
        return patchManifest;
    }
    public static String patchConfiguration() throws Exception{return patches().configuration();}
    public static String[] patchLayers() throws Exception{return patches().layerRows();}

    private static String key(String text) {
        return text.replace('\\', '/').toLowerCase(Locale.ROOT);
    }

    private static HashMap<String, String> children(String id) throws Exception {
        if (directories.containsKey(id)) return directories.get(id);
        HashMap<String, String> values = new HashMap<String, String>();
        Uri uri = DocumentsContract.buildChildDocumentsUriUsingTree(tree, id);
        try (Cursor cursor = context.getContentResolver().query(uri, new String[] {
                DocumentsContract.Document.COLUMN_DOCUMENT_ID,
                DocumentsContract.Document.COLUMN_DISPLAY_NAME}, null, null, null)) {
            if (cursor == null) throw new IllegalStateException("cannot enumerate document directory");
            while (cursor.moveToNext()) values.put(key(cursor.getString(1)), cursor.getString(0));
        }
        directories.put(id, values);
        return values;
    }

    private static Uri resolve(String path) throws Exception {
        String current = rootId;
        for (String component : path.replace('\\', '/').split("/")) {
            if (component.length() == 0 || component.equals(".")) continue;
            if (component.equals("..")) throw new SecurityException("parent document path");
            current = children(current).get(key(component));
            if (current == null) return null;
        }
        return DocumentsContract.buildDocumentUriUsingTree(tree, current);
    }

    public static String[] archives() throws Exception {
        ArrayList<String> rows = new ArrayList<String>();
        PatchManifest patch=patches();
        ArrayList<String> paths=new ArrayList<String>(patch.archives);
        java.util.HashSet<String> imageOnly=new java.util.HashSet<String>();
        for(String name:patch.imageArchives)if(!paths.contains(name)){
            paths.add(name);imageOnly.add(name);
        }
        ArrayList<String> localArchives=new ArrayList<String>(patch.localArchives);
        for(String name:patch.imageArchives)if(!localArchives.contains(name))localArchives.add(name);
        if(!patch.explicit){
            Uri overlay=resolve("patches/local/archives");
            ArrayList<String> names=new ArrayList<String>();
            if(overlay!=null)for(String name:children(DocumentsContract.getDocumentId(overlay)).keySet())
                if(name.endsWith(".arc"))names.add("patches/local/archives/"+name);
            java.util.Collections.sort(names,java.util.Collections.reverseOrder());
            localArchives.addAll(names);
            if(patch.legacy)paths.addAll(names);
        }
        ArrayList<String> base=new ArrayList<String>(children(rootId).keySet());
        java.util.Collections.sort(base,java.util.Collections.reverseOrder());
        java.util.LinkedHashMap<String,String> duplicates=ArchiveLayerPolicy.duplicates(base,localArchives,
            new ArchiveLayerPolicy.Resources(){
                @Override public long size(String path) throws Exception {
                    Uri uri=resolve(path);
                    if(uri==null)return -1;
                    try(ParcelFileDescriptor fd=context.getContentResolver().openFileDescriptor(uri,"r")){
                        if(fd==null)throw new IllegalStateException("cannot open archive");
                        long length=fd.getStatSize();
                        return length>=0?length:Os.lseek(fd.getFileDescriptor(),0,OsConstants.SEEK_END);
                    }
                }
                @Override public InputStream open(String path) throws Exception {
                    Uri uri=resolve(path);
                    return uri==null?null:context.getContentResolver().openInputStream(uri);
                }
            });
        duplicateArchives.clear();
        for(java.util.Map.Entry<String,String> entry:duplicates.entrySet())
            duplicateArchives.put(key(entry.getKey()),entry.getValue());
        for(String name:base){
            if(duplicates.containsKey(name)){
                DiagnosticLog.write("archive","base copy skipped: "+name+" = "+duplicates.get(name));
            }else paths.add(name);
        }
        for (String name : paths) {
            if (!name.endsWith(".arc") || name.equals("savedata.arc")) continue;
            Uri uri = resolve(name);
            if(uri==null)throw new IllegalArgumentException("declared patch archive missing: "+name);
            ParcelFileDescriptor fd = context.getContentResolver().openFileDescriptor(uri, "r");
            if (fd == null) throw new IllegalStateException("cannot open archive");
            long length = fd.getStatSize();
            if (length < 0) length = Os.lseek(fd.getFileDescriptor(), 0, OsConstants.SEEK_END);
            rows.add(name + "\t" + fd.detachFd() + "\t" + length+
                (imageOnly.contains(name)?"\timages":""));
        }
        return rows.toArray(new String[rows.size()]);
    }
    public static String openArchive(String name) throws Exception{
        if(duplicateArchives.containsKey(key(name))){
            DiagnosticLog.write("archive","duplicate local patch registration skipped: "+name);
            return "ABSENT";
        }
        Uri uri=resolve(name);
        if(uri==null){
            String stem=name.toLowerCase(Locale.ROOT).endsWith(".arc")?name.substring(0,name.length()-4):name;
            if(resolve(stem)!=null)throw new IllegalStateException("dynamic directory archive provider unavailable: "+stem);
            DiagnosticLog.write("archive","optional resource absent: "+name);
            return "ABSENT";
        }
        ParcelFileDescriptor fd=context.getContentResolver().openFileDescriptor(uri,"r");
        if(fd==null)throw new IllegalStateException("cannot open dynamic archive");
        try{
            long length=fd.getStatSize();
            if(length<0)length=Os.lseek(fd.getFileDescriptor(),0,OsConstants.SEEK_END);
            return fd.detachFd()+"\t"+length;
        }finally{fd.close();}
    }
    public static String[] resourceNames() throws Exception{
        ArrayList<String> names=new ArrayList<String>();
        for(String name:children(rootId).keySet()){
            int dot=name.lastIndexOf('.');
            String ext=dot<0?"":name.substring(dot+1);
            if(java.util.Arrays.asList("mjo","cfg","env","png","rct","rc8","wav","ogg","wma","dat","txt").contains(ext))
                names.add(name);
        }
        for(PatchManifest.Layer layer:patches().layers){
                String path=layer.directory;
                Uri overlay=resolve(path);
                if(overlay!=null)for(String name:children(DocumentsContract.getDocumentId(overlay)).keySet()){
                    if(name.endsWith(".mjo")||name.endsWith(".cfg"))if(!names.contains(name))names.add(name);
                }
        }
        return names.toArray(new String[names.size()]);
    }

    public static String[] engineFeatures() throws Exception {
        ArrayList<String> paths = new ArrayList<String>();
        for (String name : children(rootId).keySet()) {
            if (name.endsWith(".exe")) paths.add(name);
        }
        String bin = children(rootId).get("bin");
        if (bin != null) {
            for (String name : children(bin).keySet()) {
                if (name.endsWith(".exe")) paths.add("bin/" + name);
            }
        }
        java.util.Collections.sort(paths);
        ArrayList<String> rows = new ArrayList<String>();
        byte[] buffer = new byte[65536];
        for (String path : paths) {
            ByteArrayOutputStream implementation = new ByteArrayOutputStream();
            long length = 0;
            try (InputStream input = context.getContentResolver().openInputStream(resolve(path))) {
                if (input == null) throw new IllegalStateException("cannot fingerprint game engine");
                int count;
                while ((count = input.read(buffer)) != -1) {
                    length += count;
                    if (length > 128L * 1024 * 1024)
                        throw new IllegalStateException("engine fingerprint exceeds read limit: " + path);
                    implementation.write(buffer, 0, count);
                }
            }catch(Exception unavailable){
                DiagnosticLog.write("engine","optional engine version hints unavailable: "+path);
                continue;
            }
            for(String feature:EngineFeatures.from(implementation.toByteArray()))
                if(!rows.contains(feature))rows.add(feature);
        }
        return rows.toArray(new String[rows.size()]);
    }

    public static byte[] readFile(String name) throws Exception {
        if (name.startsWith("save://")) {
            File file = saved(name.substring(7));
            if (!file.isFile()) return null;
            try (InputStream input = new FileInputStream(file)) { return readBounded(input); }
        }
        PatchManifest manifest=patches();
        if(name.startsWith("patches/images/")){
            if(manifest.imageDirectory.isEmpty())return null;
            name=manifest.imageDirectory+name.substring("patches/images".length());
        }else if(name.startsWith("patches/fonts/"))
            name=manifest.fontDirectory+name.substring("patches/fonts".length());
        Uri uri = resolve(name);
        if (uri == null) return null;
        try (InputStream input = context.getContentResolver().openInputStream(uri)) {
            if (input == null) return null;
            return readBounded(input);
        }
    }

    public static byte[] asset(String path) throws Exception {
        try (InputStream input = context.getAssets().open(path)) { return readBounded(input); }
    }
    public static String service(String action,String value) throws Exception{
        if(action.equals("checker_begin"))return ExternalChecker.begin((Activity)context,value,tree);
        if(action.equals("checker_poll"))return ExternalChecker.poll(value);
        if(action.equals("checker_cancel"))return ExternalChecker.cancel(value);
        if(action.equals("font_quality")){
            fontQualityMode=Integer.parseInt(value);return "OK";
        }
        if(action.equals("system_lcid")){
            Locale locale=Locale.getDefault();String language=locale.getLanguage(),country=locale.getCountry();
            int result=0x1000;
            if(language.equals("ja"))result=0x411;
            else if(language.equals("zh"))result=country.equals("TW")?0x404:country.equals("HK")?0xc04:country.equals("SG")?0x1004:0x804;
            else if(language.equals("en"))result=country.equals("GB")?0x809:country.equals("AU")?0xc09:country.equals("CA")?0x1009:0x409;
            else if(language.equals("ko"))result=0x412;
            else if(language.equals("de"))result=0x407;
            else if(language.equals("fr"))result=0x40c;
            else if(language.equals("es"))result=0xc0a;
            else if(language.equals("ru"))result=0x419;
            else if(language.equals("it"))result=0x410;
            else if(language.equals("pt"))result=country.equals("BR")?0x416:0x816;
            return Integer.toString(result);
        }
        if(action.equals("force_key_paste")){
            final Activity owner=(Activity)context;
            final String[] response={null};
            final CountDownLatch completed=new CountDownLatch(1);
            Runnable paste=new Runnable(){@Override public void run(){
                try{
                    android.view.View focused=owner.getCurrentFocus();
                    if(!owner.hasWindowFocus()||!(focused instanceof android.widget.TextView)
                        ||!((android.widget.TextView)focused).onTextContextMenuItem(android.R.id.paste))
                        response[0]="error no focused editable text target accepted paste";
                    else response[0]="OK";
                }catch(RuntimeException error){response[0]="error paste failed: "+error;}
                finally{completed.countDown();}
            }};
            if(android.os.Looper.myLooper()==android.os.Looper.getMainLooper())paste.run();
            else owner.runOnUiThread(paste);
            completed.await();
            if(!"OK".equals(response[0]))throw new IllegalStateException(response[0]);
            return "OK";
        }
        if(action.equals("open_url")||action.equals("open_help")){
            Uri uri=action.equals("open_url")?Uri.parse(value):resolve(value);
            if(uri==null)throw new IllegalArgumentException("help document unavailable");
            android.content.Intent intent=new android.content.Intent(android.content.Intent.ACTION_VIEW,uri);
            intent.addFlags(android.content.Intent.FLAG_GRANT_READ_URI_PERMISSION);
            if(intent.resolveActivity(context.getPackageManager())==null)
                throw new IllegalStateException("no application can open requested document");
            context.startActivity(intent);return "OK";
        }
        if(action.equals("clipboard_set")){
            clipboard.setPrimaryClip(android.content.ClipData.newPlainText("",value));return "OK";
        }
        if(action.equals("clipboard_get")){
            android.content.ClipData data=clipboard.getPrimaryClip();
            if(data==null||data.getItemCount()==0)return "";
            CharSequence text=data.getItemAt(0).coerceToText(context);
            return text==null?"":text.toString();
        }
        if(action.equals("clipboard_changed")){
            boolean changed=clipboardChanged;clipboardChanged=false;return changed?"1":"0";
        }
        throw new IllegalArgumentException("unsupported host service: "+action);
    }
    private static File scriptFile(String name) throws Exception {
        name=name.replace('\\','/');
        if(name.length()==0||name.length()>1024||name.startsWith("/")||name.indexOf(':')>=0||name.indexOf('\0')>=0)
            throw new IllegalArgumentException("invalid script output path");
        for(String part:name.split("/"))if(part.equals(".."))
            throw new IllegalArgumentException("parent script output path");
        File base=new File(gameFiles,"script-files");
        File file=new File(base,name);
        if(!file.getCanonicalPath().startsWith(base.getCanonicalPath()+File.separator))
            throw new IllegalArgumentException("script output escaped game directory");
        return file;
    }
    public static byte[] readScriptFile(String name) throws Exception {
        File file=scriptFile(name);
        if(!file.isFile())return null;
        try(InputStream input=new FileInputStream(file)){return readBounded(input);}
    }
    public static void writeScriptFile(String name,byte[] data) throws Exception {
        if(data.length>64*1024*1024)throw new IllegalArgumentException("script output size limit");
        File file=scriptFile(name),parent=file.getParentFile();
        if(!parent.isDirectory()&&!parent.mkdirs())throw new IllegalStateException("script output directory");
        File pending=new File(parent,file.getName()+".writing");
        try {
            try(FileOutputStream output=new FileOutputStream(pending)){
                output.write(data);output.getFD().sync();
            }
            Os.rename(pending.getAbsolutePath(),file.getAbsolutePath());
        }finally{if(pending.exists())pending.delete();}
    }

    private static byte[] readBounded(InputStream input) throws Exception {
        ByteArrayOutputStream output = new ByteArrayOutputStream();
        byte[] buffer = new byte[65536];
        int count;
        while ((count = input.read(buffer)) != -1) {
            if (output.size() + count > 64 * 1024 * 1024)
                throw new IllegalStateException("resource exceeds memory read limit");
            output.write(buffer, 0, count);
        }
        return output.toByteArray();
    }

    public static byte[] convert(byte[] text, String from, String to) throws Exception {
        Charset source = Charset.forName(from), target = Charset.forName(to);
        java.nio.charset.CharsetDecoder decoder = source.newDecoder()
            .onMalformedInput(java.nio.charset.CodingErrorAction.REPORT)
            .onUnmappableCharacter(java.nio.charset.CodingErrorAction.REPORT);
        java.nio.charset.CharsetEncoder encoder = target.newEncoder()
            .onMalformedInput(java.nio.charset.CodingErrorAction.REPORT)
            .onUnmappableCharacter(java.nio.charset.CodingErrorAction.REPORT);
        java.nio.ByteBuffer bytes = encoder.encode(decoder.decode(java.nio.ByteBuffer.wrap(text)));
        byte[] result = new byte[bytes.remaining()]; bytes.get(result); return result;
    }

    public static int[] bitmap(byte[] encoded) {
        Bitmap bitmap = BitmapFactory.decodeByteArray(encoded, 0, encoded.length);
        if (bitmap == null) throw new IllegalStateException("Android bitmap decoder rejected image");
        int w = bitmap.getWidth(), h = bitmap.getHeight();
        if ((long) w * h > 16 * 1024 * 1024) {
            bitmap.recycle(); throw new IllegalStateException("bitmap memory limit");
        }
        int[] result = new int[2 + w * h];
        result[0] = w; result[1] = h;
        bitmap.getPixels(result, 2, w, 0, 0, w, h);
        bitmap.recycle(); return result;
    }

    public static int[] glyphs(String face, int height, int[] steps, String message, boolean boxed) {
        final int quality=fontQualityMode==0?1:4;
        int[] codepoints = message.codePoints().toArray();
        int half = boxed ? steps[0] : 0;
        int boxw = boxed ? steps[1] : 0;
        Paint measure = new Paint(Paint.ANTI_ALIAS_FLAG | Paint.SUBPIXEL_TEXT_FLAG);
        Typeface privateFace = privateTypefaces.get(key(face));
        measure.setTypeface(PatchTypography.apply(face, privateFace != null ? privateFace :
            customTypeface != null ? customTypeface : Typeface.create(PatchTypography.family(face), Typeface.NORMAL)));
        measure.setTextSize(height);
        ArrayList<Integer> advances = new ArrayList<Integer>();
        ArrayList<Integer> xs = new ArrayList<Integer>(), ys = new ArrayList<Integer>();
        int x = 0, y = 0, max = 0;
        for (int i = 0; i < codepoints.length; ++i) {
            int cp = codepoints[i];
            int advance = boxed ? (half == 0
                ? (int)Math.ceil(measure.measureText(new String(Character.toChars(cp))))
                : (cp < 128 ? half : half * 2)) : steps[i];
            if (cp == '\n' || (boxed && boxw > 0 && x != 0 && x + advance > boxw)) {
                max = Math.max(max, x); x = 0; y += height;
                if (cp == '\n') { advances.add(0); xs.add(-1); ys.add(y); continue; }
            }
            advances.add(advance); xs.add(x); ys.add(y); x += advance;
        }
        int w = Math.max(1, Math.max(max, x)), h = Math.max(height, y + height);
        if ((long) w * h > 16 * 1024 * 1024) throw new IllegalStateException("glyph raster memory limit");
        Bitmap bitmap = Bitmap.createBitmap(w * quality, h * quality, Bitmap.Config.ARGB_8888);
        Canvas canvas = new Canvas(bitmap);
        Paint paint = new Paint(Paint.ANTI_ALIAS_FLAG | Paint.SUBPIXEL_TEXT_FLAG);
        if (customTypeface == null) {
            try {
                byte[] bytes = readFile("patches/fonts/SourceHanSansSC-Regular.otf");
                if (bytes != null) {
                    File file = new File(context.getCacheDir(), "SourceHanSansSC-Regular.otf");
                    if (!file.isFile() || file.length() != bytes.length)
                        try (FileOutputStream output = new FileOutputStream(file)) { output.write(bytes); }
                    customTypeface = Typeface.createFromFile(file);
                }
            } catch (Exception error) { throw new IllegalStateException("private font load failed", error); }
        }
        paint.setTypeface(PatchTypography.apply(face, privateFace!=null?privateFace:
            customTypeface != null ? customTypeface : Typeface.create(PatchTypography.family(face), Typeface.NORMAL)));
        paint.setTextSize(height * quality);
        paint.setColor(Color.WHITE);
        GlyphCell cell=GlyphCell.measure(paint,codepoints,height*quality,quality);
        for (int i = 0; i < codepoints.length; ++i) {
            if (xs.get(i) < 0) continue;
            String glyph = new String(Character.toChars(codepoints[i]));
            float width = paint.measureText(glyph);
            canvas.save();
            canvas.translate(xs.get(i) * quality, ys.get(i) * quality+cell.baseline);
            canvas.scale(width>0?(advances.get(i)*quality)/width:1,cell.verticalScale);
            canvas.drawText(glyph, 0, 0, paint);
            canvas.restore();
        }
        int samples=quality*quality;
        int[] source = new int[w * h * samples], result = new int[w * h + 2];
        bitmap.getPixels(source, 0, w * quality, 0, 0, w * quality, h * quality); bitmap.recycle();
        result[0] = w; result[1] = h;
        for (int yy = 0; yy < h; ++yy) for (int xx = 0; xx < w; ++xx) {
            int total = 0;
            for (int sy = 0; sy < quality; ++sy) for (int sx = 0; sx < quality; ++sx)
                total += source[(yy * quality + sy) * w * quality + xx * quality + sx] >>> 24;
            result[2 + yy * w + xx] = (total + samples/2) / samples;
        }
        return result;
    }

    private static File saved(String name) {
        if (!name.matches("[A-Za-z0-9_-][A-Za-z0-9_.-]{0,119}"))
            throw new IllegalArgumentException("invalid save filename");
        return new File(gameFiles, name);
    }

    private static String hex(byte[] bytes) {
        StringBuilder result = new StringBuilder();
        for (byte value : bytes) result.append(String.format(Locale.ROOT, "%02x", value & 255));
        return result.toString();
    }
    public static String hash(byte[] bytes) throws Exception {
        return hex(MessageDigest.getInstance("SHA-256").digest(bytes));
    }
    public static String mediaError() { return mediaError; }
    public static String modalDialog(String action, String token, int flags, String message, String caption) {
        if (action.equals("poll")) return modalDialogs.poll(token);
        if(action.equals("cancel")){
            if(!modalDialogs.cancel(token))return "error dialog token mismatch";
            hideDialog();return "OK";
        }
        if (!action.equals("begin")) throw new IllegalArgumentException("unknown dialog operation");
        modalDialogs.begin(token, flags, message, caption);
        scheduleDialog();
        return "pending";
    }
    private static void scheduleDialog() {
        final Activity activity = (Activity) context;
        activity.runOnUiThread(new Runnable() {
            @Override public void run() {
                final ModalDialogState.Request request = modalDialogs.visible();
                if (request == null || (activeDialogRequest == request && activeDialog != null)) return;
                if (activity.isFinishing() || activity.isDestroyed()) {
                    modalDialogs.fail(request, "Activity unavailable for native dialog"); return;
                }
                try {
                    if ((request.flags >>> 16) == 5) {
                        activeDialogRequest=request;
                        activeDialog=NativeSlotDialog.show(activity,request,modalDialogs,name->{
                            if(!name.matches("thumb_[A-Za-z0-9_-]{1,100}\\.png"))
                                throw new IllegalArgumentException("invalid native preview filename");
                            return BitmapFactory.decodeFile(saved(name).getAbsolutePath());
                        });
                        return;
                    }
                    if ((request.flags >>> 16) != 0) {
                        final int kind = request.flags >>> 16;
                        final android.widget.LinearLayout form = new android.widget.LinearLayout(activity);
                        form.setOrientation(android.widget.LinearLayout.VERTICAL);
                        int margin = (int)(16 * activity.getResources().getDisplayMetrics().density);
                        form.setPadding(margin, margin, margin, margin);
                        final ArrayList<android.widget.EditText> editors = new ArrayList<android.widget.EditText>();
                        final ArrayList<String> keys = new ArrayList<String>();
                        String[] rows = kind == 1 ? new String[] {request.draft} : request.draft.split("\n");
                        for (String row : rows) {
                            if (kind == 5 && row.indexOf('=') < 0) {
                                android.widget.TextView saved = new android.widget.TextView(activity);
                                saved.setText(row); form.addView(saved); continue;
                            }
                            final android.widget.EditText edit = new android.widget.EditText(activity);
                            if (kind == 1) {
                                edit.setText(row);
                                edit.setFilters(new android.text.InputFilter[] {new android.text.InputFilter.LengthFilter(2047)});
                            } else {
                                int split = row.indexOf('=');
                                if (split < 1) throw new IllegalArgumentException("invalid native settings row");
                                String key = row.substring(0, split); keys.add(key);
                                android.widget.TextView label = new android.widget.TextView(activity);
                                String title = key.equals("musicvolume") ? "Music volume" :
                                    key.equals("soundvolume") ? "Sound volume" :
                                    key.equals("voicevolume") ? "Voice volume" :
                                    key.equals("mojispeed") ? "Text speed" :
                                    key.equals("autospeed") ? "Auto speed" : key.equals("slot") ? "Save slot" : "Effect speed";
                                label.setText(title); form.addView(label);
                                edit.setInputType(android.text.InputType.TYPE_CLASS_NUMBER);
                                edit.setText(row.substring(split + 1));
                            }
                            editors.add(edit); form.addView(edit);
                        }
                        final Runnable saveDraft = new Runnable() {
                            @Override public void run() {
                                StringBuilder data = new StringBuilder();
                                for (int i = 0; i < editors.size(); ++i) {
                                    if (i > 0) data.append('\n');
                                    if (kind != 1) data.append(keys.get(i)).append('=');
                                    data.append(editors.get(i).getText());
                                }
                                request.draft = data.toString();
                            }
                        };
                        for (android.widget.EditText edit : editors)
                            edit.addTextChangedListener(new android.text.TextWatcher() {
                                @Override public void beforeTextChanged(CharSequence s, int start, int count, int after) {}
                                @Override public void onTextChanged(CharSequence s, int start, int before, int count) { saveDraft.run(); }
                                @Override public void afterTextChanged(android.text.Editable text) {}
                            });
                        android.widget.ScrollView scroll = new android.widget.ScrollView(activity);
                        scroll.addView(form);
                        AlertDialog.Builder builder = new AlertDialog.Builder(activity).setCancelable(false)
                            .setTitle(kind == 1 ? request.message : request.caption).setView(scroll)
                            .setPositiveButton(android.R.string.ok, new DialogInterface.OnClickListener() {
                                @Override public void onClick(DialogInterface dialog, int which) {
                                    saveDraft.run(); modalDialogs.completeData(request, request.draft);
                                    if (activeDialogRequest == request) {
                                        activeDialog = null; activeDialogRequest = null;
                                    }
                                }
                            }).setNegativeButton(android.R.string.cancel, new DialogInterface.OnClickListener() {
                                @Override public void onClick(DialogInterface dialog, int which) {
                                    modalDialogs.completeData(request, "");
                                    if (activeDialogRequest == request) {
                                        activeDialog = null; activeDialogRequest = null;
                                    }
                                }
                            });
                        activeDialogRequest = request; activeDialog = builder.create(); activeDialog.show();
                        return;
                    }
                    final int buttons = request.flags & 15;
                    AlertDialog.Builder builder = new AlertDialog.Builder(activity)
                        .setTitle(request.caption).setMessage(request.message).setCancelable(false);
                    int icon = request.flags & 0xf0;
                    if (icon == 16 || icon == 48) builder.setIcon(android.R.drawable.ic_dialog_alert);
                    else if (icon == 32) builder.setIcon(android.R.drawable.ic_dialog_info);
                    final int positive = buttons == 4 ? 6 : buttons == 5 ? 4 : 1;
                    DialogInterface.OnClickListener yes = new DialogInterface.OnClickListener() {
                        @Override public void onClick(DialogInterface dialog, int which) {
                            modalDialogs.complete(request, positive);
                            if (activeDialogRequest == request) {
                                activeDialog = null; activeDialogRequest = null;
                            }
                        }
                    };
                    if (buttons == 4) builder.setPositiveButton(android.R.string.yes, yes);
                    else if (buttons == 5) builder.setPositiveButton("Retry", yes);
                    else builder.setPositiveButton(android.R.string.ok, yes);
                    if (buttons != 0) {
                        builder.setNegativeButton(buttons == 4 ? android.R.string.no : android.R.string.cancel,
                            new DialogInterface.OnClickListener() {
                                @Override public void onClick(DialogInterface dialog, int which) {
                                    modalDialogs.complete(request, buttons == 4 ? 7 : 2);
                                    if (activeDialogRequest == request) {
                                        activeDialog = null; activeDialogRequest = null;
                                    }
                                }
                            });
                    }
                    activeDialogRequest = request;
                    activeDialog = builder.create();
                    activeDialog.show();
                } catch (RuntimeException error) {
                    modalDialogs.fail(request, "native dialog UI failed: " + error);
                }
            }
        });
    }
    private static void hideDialog() {
        final Activity activity = (Activity) context;
        activity.runOnUiThread(new Runnable() {
            @Override public void run() {
                if (activeDialog != null) activeDialog.dismiss();
                activeDialog = null; activeDialogRequest = null;
            }
        });
    }
    public static void fontFilter(String filter) { fontNameFilter = filter; }
    public static String fontDialog(final String current, final int proportional) throws Exception {
        java.util.TreeSet<String> candidates = new java.util.TreeSet<String>();
        candidates.add(current); candidates.addAll(privateTypefaces.keySet());
        java.util.Collections.addAll(candidates, "sans-serif", "serif", "monospace",
            "sans-serif-condensed", "sans-serif-light");
        final ArrayList<String> choices = new ArrayList<String>();
        Paint probe = new Paint(); probe.setTextSize(24);
        for (String face : candidates) {
            if (face.length() == 0 || face.startsWith("@")) continue;
            Typeface privateFace = privateTypefaces.get(key(face));
            probe.setTypeface(privateFace != null ? privateFace : Typeface.create(face, Typeface.NORMAL));
            if (!probe.hasGlyph("\u3042")) continue;
            boolean fixed = Math.abs(probe.measureText("M") - probe.measureText("i")) < .01f;
            if (!face.contains(fontNameFilter) && proportional == 0 && !fixed) continue;
            choices.add(face);
        }
        if (choices.isEmpty()) throw new IllegalStateException("no font matches the native chooser filter");
        final String[] selected = {current};
        final CountDownLatch completed = new CountDownLatch(1);
        ((Activity) context).runOnUiThread(new Runnable() {
            @Override public void run() {
                final String[] names = choices.toArray(new String[choices.size()]);
                new AlertDialog.Builder(context).setTitle("选择字体")
                    .setItems(names, new DialogInterface.OnClickListener() {
                        @Override public void onClick(DialogInterface dialog, int which) {
                            selected[0] = names[which]; completed.countDown();
                        }
                    }).setNegativeButton("取消", new DialogInterface.OnClickListener() {
                        @Override public void onClick(DialogInterface dialog, int which) { completed.countDown(); }
                    }).setOnCancelListener(new DialogInterface.OnCancelListener() {
                        @Override public void onCancel(DialogInterface dialog) { completed.countDown(); }
                    }).show();
            }
        });
        completed.await();
        return selected[0];
    }
    public static void debug(String text) throws Exception {
        try (FileOutputStream output = new FileOutputStream(new File(gameFiles, "last-run.txt"))) {
            output.write(text.getBytes(StandardCharsets.UTF_8));
        }
        android.util.Log.i("MajiroPlayer", text);
    }
    public static void diagnosticOutput(String text, String script, int offset) throws Exception {
        String line = script + "@" + Integer.toHexString(offset) + ": " + text + "\n";
        try (FileOutputStream output = new FileOutputStream(new File(gameFiles, "debugout.log"), true)) {
            output.write(line.getBytes(StandardCharsets.UTF_8));
        }
        android.util.Log.i("MajiroScript", line);
    }
    public static boolean loadFont(String name) throws Exception {
        byte[] bytes = readFile(name);
        if (bytes == null) bytes = readFile("font/" + name);
        if (bytes == null) return false;
        String identity = hex(MessageDigest.getInstance("SHA-256").digest(bytes));
        File directory = new File(context.getCacheDir(), "private-fonts");
        if (!directory.isDirectory() && !directory.mkdirs()) throw new IllegalStateException("font cache directory");
        File file = new File(directory, identity + ".font");
        if (!file.isFile()) try (FileOutputStream output = new FileOutputStream(file)) { output.write(bytes); }
        Typeface loaded = Typeface.createFromFile(file);
        privateTypefaces.put(key(name), loaded);
        java.nio.ByteBuffer sfnt = java.nio.ByteBuffer.wrap(bytes).order(java.nio.ByteOrder.BIG_ENDIAN);
        if (bytes.length >= 12 && (sfnt.getInt(0) == 0x00010000 || sfnt.getInt(0) == 0x4f54544f)) {
            int tables = sfnt.getShort(4) & 65535;
            if (12L + tables * 16L > bytes.length) throw new IllegalArgumentException("font table directory");
            for (int i = 0; i < tables; ++i) {
                int at = 12 + i * 16;
                if (sfnt.getInt(at) != 0x6e616d65) continue;
                int start = sfnt.getInt(at + 8), length = sfnt.getInt(at + 12);
                if (start < 0 || length < 6 || (long)start + length > bytes.length)
                    throw new IllegalArgumentException("font name table");
                int count = sfnt.getShort(start + 2) & 65535;
                int strings = sfnt.getShort(start + 4) & 65535;
                if (6L + count * 12L > length) throw new IllegalArgumentException("font name records");
                for (int record = 0; record < count; ++record) {
                    int row = start + 6 + record * 12;
                    int platform = sfnt.getShort(row) & 65535, id = sfnt.getShort(row + 6) & 65535;
                    int size = sfnt.getShort(row + 8) & 65535, offset = sfnt.getShort(row + 10) & 65535;
                    if ((id != 1 && id != 4 && id != 16) || platform != 3) continue;
                    if ((long)strings + offset + size > length) throw new IllegalArgumentException("font name string");
                    String family = new String(bytes, start + strings + offset, size, StandardCharsets.UTF_16BE);
                    privateTypefaces.put(key(family), loaded);
                }
            }
        }
        return true;
    }
    public static int wallpaper(int width, int height, int[] argb, int style) throws Exception {
        Bitmap bitmap = Bitmap.createBitmap(argb, width, height, Bitmap.Config.ARGB_8888);
        try {
            android.app.WallpaperManager manager = android.app.WallpaperManager.getInstance(context);
            if (!manager.isWallpaperSupported() || !manager.isSetWallpaperAllowed()) return 0;
            int desiredWidth = Math.max(width, manager.getDesiredMinimumWidth());
            int desiredHeight = Math.max(height, manager.getDesiredMinimumHeight());
            Bitmap output = bitmap;
            if (style == 1) {
                output = Bitmap.createBitmap(desiredWidth, desiredHeight, Bitmap.Config.ARGB_8888);
                Canvas canvas = new Canvas(output);
                for (int y = 0; y < desiredHeight; y += height)
                    for (int x = 0; x < desiredWidth; x += width) canvas.drawBitmap(bitmap, x, y, null);
            } else if (style == 2) output = Bitmap.createScaledBitmap(bitmap, desiredWidth, desiredHeight, true);
            else {
                output = Bitmap.createBitmap(desiredWidth, desiredHeight, Bitmap.Config.ARGB_8888);
                Canvas canvas = new Canvas(output); canvas.drawColor(Color.BLACK);
                canvas.drawBitmap(bitmap, (desiredWidth-width)/2f, (desiredHeight-height)/2f, null);
            }
            try { manager.setBitmap(output, null, true, android.app.WallpaperManager.FLAG_SYSTEM); }
            finally { if (output != bitmap) output.recycle(); }
            return 1;
        } finally { bitmap.recycle(); }
    }

    public static String storage(String request) throws Exception {
        int newline = request.indexOf('\n');
        String head = newline < 0 ? request : request.substring(0, newline);
        String body = newline < 0 ? "" : request.substring(newline + 1);
        if (head.equals("sha256"))
            return "OK " + hex(MessageDigest.getInstance("SHA-256").digest(body.getBytes(StandardCharsets.UTF_8)));
        int split = head.indexOf(' ');
        String action = split < 0 ? head : head.substring(0, split);
        String name = split < 0 ? "" : head.substring(split + 1);
        File file = saved(name);
        if (action.equals("save_exists")) return file.isFile() ? "OK 1" : "OK 0";
        if (action.equals("save_read")) {
            if (!file.isFile()) return "OK absent";
            try (InputStream input = new FileInputStream(file)) {
                return "OK data\n" + new String(readBounded(input), StandardCharsets.UTF_8);
            }
        }
        if (action.equals("save_delete")) {
            if (file.exists() && !file.delete()) throw new IllegalStateException("save deletion failed");
            return "OK";
        }
        if (action.equals("save_write")) {
            File pending = saved(name + ".writing");
            try (FileOutputStream output = new FileOutputStream(pending)) {
                output.write(body.getBytes(StandardCharsets.UTF_8)); output.getFD().sync();
            }
            Os.rename(pending.getAbsolutePath(), file.getAbsolutePath());
            return "OK";
        }
        throw new IllegalArgumentException("unknown storage operation: " + action);
    }

    public static void savePng(String name, int width, int height, int[] argb) throws Exception {
        if (!name.startsWith("save://")) throw new IllegalArgumentException("PNG outside save directory");
        Bitmap bitmap = Bitmap.createBitmap(argb, width, height, Bitmap.Config.ARGB_8888);
        File destination = saved(name.substring(7)), pending = saved(name.substring(7) + ".writing");
        try (FileOutputStream output = new FileOutputStream(pending)) {
            if (!bitmap.compress(Bitmap.CompressFormat.PNG, 0, output))
                throw new IllegalStateException("PNG encode failed");
            output.getFD().sync();
        } finally { bitmap.recycle(); }
        Os.rename(pending.getAbsolutePath(), destination.getAbsolutePath());
    }

    public static String cacheMedia(String name, byte[] bytes) throws Exception {
        File directory = new File(context.getCacheDir(), "media");
        if (!directory.isDirectory() && !directory.mkdirs()) throw new IllegalStateException("media cache");
        String suffix = name.toLowerCase(Locale.ROOT);
        suffix = suffix.contains(".") ? suffix.substring(suffix.lastIndexOf('.')) : ".bin";
        String hash = hex(MessageDigest.getInstance("SHA-256").digest(bytes));
        File file = new File(directory, hash + suffix);
        if (!file.isFile()) try (FileOutputStream output = new FileOutputStream(file)) { output.write(bytes); }
        mediaFiles.put(file.getAbsolutePath(), file);
        return file.getAbsolutePath();
    }

    public static long duration(String path) throws Exception {
        MediaMetadataRetriever metadata = new MediaMetadataRetriever();
        try {
            metadata.setDataSource(path);
            return Long.parseLong(metadata.extractMetadata(MediaMetadataRetriever.METADATA_KEY_DURATION));
        } finally { metadata.release(); }
    }
    public static String[] movies() throws Exception {
        String directory = children(rootId).get("movie");
        if (directory == null) return new String[0];
        ArrayList<String> rows = new ArrayList<String>();
        for (String name : children(directory).keySet()) {
            Uri uri = resolve("movie/" + name);
            if (name.endsWith(".wmv")) {
                rows.add(name + "\t" + asfDuration(uri));
                continue;
            }
            rows.add(name + "\t" + IjkVideo.duration(context, uri));
        }
        return rows.toArray(new String[rows.size()]);
    }
    private static byte[] exact(InputStream input, int length) throws Exception {
        byte[] result = new byte[length];
        int offset = 0;
        while (offset < length) {
            int count = input.read(result, offset, length - offset);
            if (count < 0) throw new IllegalStateException("truncated ASF metadata");
            offset += count;
        }
        return result;
    }
    private static long little64(byte[] data, int offset) {
        long result = 0;
        for (int i = 7; i >= 0; --i) result = (result << 8) | (data[offset + i] & 255);
        return result;
    }
    private static void skipExact(InputStream input, long length) throws Exception {
        while (length > 0) {
            long skipped = input.skip(length);
            if (skipped == 0) {
                if (input.read() == -1) throw new IllegalStateException("truncated ASF object");
                skipped = 1;
            }
            length -= skipped;
        }
    }
    private static long asfDuration(Uri uri) throws Exception {
        try (InputStream input = context.getContentResolver().openInputStream(uri)) {
            byte[] header = exact(input, 30);
            byte[] signature = {(byte)0x30,0x26,(byte)0xb2,0x75,(byte)0x8e,0x66,(byte)0xcf,0x11,
                (byte)0xa6,(byte)0xd9,0, (byte)0xaa,0,0x62,(byte)0xce,0x6c};
            for (int i = 0; i < 16; ++i)
                if (header[i] != signature[i]) throw new IllegalStateException("invalid ASF signature");
            long size = little64(header, 16);
            long count = ((long)(header[24]&255)) | ((long)(header[25]&255)<<8)
                | ((long)(header[26]&255)<<16) | ((long)(header[27]&255)<<24);
            if (size > 16 * 1024 * 1024 || count > 10000) throw new IllegalStateException("ASF header limit");
            long offset = 30;
            for (long i = 0; i < count; ++i) {
                byte[] object = exact(input, 24);
                long length = little64(object, 16);
                if (length < 24 || length > size - offset) throw new IllegalStateException("ASF object bounds");
                boolean properties = object[0] == (byte)0xa1 && object[1] == (byte)0xdc
                    && object[2] == (byte)0xab && object[3] == (byte)0x8c;
                if (properties) {
                    byte[] body = exact(input, (int)Math.min(length - 24, 80));
                    if (body.length < 64) throw new IllegalStateException("ASF file properties missing");
                    long duration = little64(body, 40) / 10000 - little64(body, 56);
                    if (duration <= 0) throw new IllegalStateException("ASF duration invalid");
                    return duration;
                }
                skipExact(input, length - 24); offset += length;
            }
            throw new IllegalStateException("ASF duration not found");
        }
    }

    public static int movie(final String action, final String path, final int volume,
                                         final boolean loop, final long from, final long to) throws Exception {
        if(action.equals("status"))return MovieTextures.status();
        if(action.equals("width"))return MovieTextures.width();
        if(action.equals("height"))return MovieTextures.height();
        if(action.equals("volume") || action.equals("texture_volume")){
            movieVolume=volume;MovieTextures.volume(volume);return 0;
        }
        if(action.equals("stop") || action.equals("texture_stop")){
            MovieTextures.stopAndWait();return 0;
        }
        if(!action.equals("play") && !action.equals("play_repeat") &&
           !action.equals("play_fromto") && !action.equals("texture_play"))
            throw new IllegalArgumentException("unknown movie operation: "+action);
        Uri uri=resolve(path);
        if(uri==null)throw new IllegalStateException("movie resource unavailable: "+path);
        movieVolume=volume;
        return MovieTextures.play(context,uri,loop,from,to,volume);
    }
    public static synchronized int[] movieFrame() throws Exception {
        return MovieTextures.frame();
    }
    public static String externalSave(String action,String data) throws Exception {
        return ExternalSaveIO.exchange((Activity)context,action,data);
    }
    public static synchronized boolean audioFrequency(String id,int hertz) throws Exception {
        MediaPlayer player=currentAudio(id);
        if(player==null)return false;
        if(hertz<=0)throw new IllegalArgumentException("audio playback frequency must be positive");
        String path=audioSourcePaths.get(id);
        if(path==null)throw new IllegalStateException("audio source rate unavailable");
        int original=0;
        android.media.MediaExtractor extractor=new android.media.MediaExtractor();
        try{
            extractor.setDataSource(path);
            for(int i=0;i<extractor.getTrackCount();++i){
                android.media.MediaFormat format=extractor.getTrackFormat(i);
                String mime=format.getString(android.media.MediaFormat.KEY_MIME);
                if(mime!=null&&mime.startsWith("audio/")&&format.containsKey(android.media.MediaFormat.KEY_SAMPLE_RATE)){
                    original=format.getInteger(android.media.MediaFormat.KEY_SAMPLE_RATE);break;
                }
            }
        }finally{extractor.release();}
        if(original<=0)throw new IllegalStateException("audio sample rate missing");
        float ratio=(float)hertz/original;
        if(ratio<0.25f||ratio>4f)throw new IllegalArgumentException("audio frequency ratio outside Android range");
        boolean wasPlaying=player.isPlaying();
        android.media.PlaybackParams params=new android.media.PlaybackParams().setSpeed(ratio).setPitch(ratio);
        player.setPlaybackParams(params);
        if(!wasPlaying&&player.isPlaying())player.pause();
        MediaPlayer next=nextPlayers.get(id);
        if(next!=null){
            boolean nextPlaying=next.isPlaying();next.setPlaybackParams(params);
            if(!nextPlaying&&next.isPlaying())next.pause();
        }
        return true;
    }
    private static final HashMap<String,String> audioSourcePaths=new HashMap<String,String>();

    private static synchronized void closeVideo() {
        MovieTextures.stop();
        movieState = 0;
        if (video != null) { video.release(); video = null; }
        if (videoSurface != null) {
            ViewGroup parent = (ViewGroup) videoSurface.getParent();
            if (parent != null) parent.removeView(videoSurface);
            videoSurface = null;
        }
    }

    public static synchronized boolean audio(String action, String id, String file, String loopFile,
                                               boolean loop, int volume, int pan, int fade, int offset,
                                               boolean initiallyPaused) throws Exception {
        MediaPlayer player = currentAudio(id);
        if (action.equals("playing")) return audioLifecycle.active(id);
        if(action.equals("completed"))return audioCompletion.containsKey(id)&&audioCompletion.get(id);
        if(action.equals("stat_reset")){audioCompletion.put(id,false);return true;}
        if(action.equals("replace")){
            if(player==null||!audioLifecycle.active(id))return true;
            action="play";
        }
        if (action.equals("pause")) return audioLifecycle.pause(id);
        if (action.equals("resume")) return audioLifecycle.resume(id);
        if (action.equals("stop")) {
            if (player != null) { audioLifecycle.removed(player); players.remove(id); player.release(); }
            MediaPlayer next = nextPlayers.remove(id);
            if (next != null) next.release();
            return true;
        }
        if (action.equals("play")) {
            audioCompletion.put(id,false);
            audioSourcePaths.put(id,file);
            if (player != null) { audioLifecycle.removed(player); players.remove(id); player.release(); }
            MediaPlayer previousNext = nextPlayers.remove(id);
            if (previousNext != null) previousNext.release();
            player = new MediaPlayer();
            final MediaPlayer created=player;
            MediaPlayer preparedNext=null;
            try {
            observeAudio(id, player);
            player.setDataSource(file); player.setLooping(loop && loopFile.length() == 0);
            player.prepare();
            players.put(id, player);
            final MediaPlayer owner = player;
            final String channel = id, second = loopFile;
            if (loopFile.length() != 0) {
                final MediaPlayer next = new MediaPlayer();
                preparedNext=next;
                observeAudio(channel, next);
                next.setDataSource(second); next.setLooping(true); next.prepare();
                applyVolume(next, volume, pan);
                nextPlayers.put(channel, next);
                player.setNextMediaPlayer(next);
                player.setOnCompletionListener(new MediaPlayer.OnCompletionListener() {
                @Override public void onCompletion(MediaPlayer completed) {
                    synchronized (GameBridge.class) {
                        if (players.get(channel) != owner) return;
                        promoteAudio(channel, owner, next);
                    }
                }
                });
            }
            applyVolume(player, volume, pan); levels.put(id, volume);
            if (offset > 0) {
                audioLifecycle.beginSeek(id, initiallyPaused);
                player.setOnSeekCompleteListener(new MediaPlayer.OnSeekCompleteListener() {
                    @Override public void onSeekComplete(MediaPlayer completed) {
                        synchronized (GameBridge.class) {
                            if (players.get(channel) != owner) return;
                            try {
                                audioLifecycle.seekComplete(channel, owner);
                                owner.setOnSeekCompleteListener(null);
                            } catch (RuntimeException error) {
                                mediaError = "audio seek completion failed: " + error;
                            }
                        }
                    }
                });
                player.seekTo(offset);
            } else if (initiallyPaused) audioLifecycle.createdPaused(id);
            else { player.start(); audioLifecycle.started(id); }
            return true;
            }catch(Exception failure){
                if(players.get(id)==created)players.remove(id);
                if(nextPlayers.get(id)==preparedNext)nextPlayers.remove(id);
                audioLifecycle.removed(created);
                try{created.release();}catch(RuntimeException ignored){}
                if(preparedNext!=null)try{preparedNext.release();}catch(RuntimeException ignored){}
                audioSourcePaths.remove(id);levels.remove(id);audioCompletion.remove(id);
                throw failure;
            }
        }
        if (player == null) {
            if (action.equals("volume") || action.equals("pan") || action.equals("fadeout")) return true;
            throw new IllegalArgumentException("unknown audio operation: " + action);
        }
        if (action.equals("volume") || action.equals("pan")) {
            if (fade <= 0 || action.equals("pan")) {
                applyVolume(player, volume, pan);
                MediaPlayer next = nextPlayers.get(id);
                if (next != null) applyVolume(next, volume, pan);
                levels.put(id, volume);
            } else {
                final MediaPlayer owner = player; final String channel = id;
                final int target = volume, position = pan, milliseconds = fade;
                final int initial = levels.containsKey(channel) ? levels.get(channel) : 1000;
                new Thread(new Runnable() {
                    @Override public void run() {
                        long began = android.os.SystemClock.uptimeMillis();
                        while (true) {
                            long elapsed = android.os.SystemClock.uptimeMillis() - began;
                            synchronized (GameBridge.class) {
                                if (players.get(channel) != owner) return;
                                int value = elapsed >= milliseconds ? target
                                    : initial + (int) ((target - initial) * elapsed / milliseconds);
                                applyVolume(owner, value, position); levels.put(channel, value);
                                MediaPlayer next = nextPlayers.get(channel);
                                if (next != null) applyVolume(next, value, position);
                                if (elapsed >= milliseconds) return;
                            }
                            android.os.SystemClock.sleep(10);
                        }
                    }
                }, "majiro-volume-fade").start();
            }
            return true;
        }
        if (action.equals("fadeout")) {
            // Fade on the actual audio player; release only that generation.
            final MediaPlayer owner = player; final String channel = id;
            final int milliseconds = Math.max(0, fade), initial = volume, position = pan;
            new Thread(new Runnable() {
                @Override public void run() {
                    long began = android.os.SystemClock.uptimeMillis();
                    while (true) {
                        long elapsed = android.os.SystemClock.uptimeMillis() - began;
                        synchronized (GameBridge.class) {
                            if (players.get(channel) != owner) return;
                            if (elapsed >= milliseconds) {
                                audioLifecycle.removed(owner); players.remove(channel); owner.release();
                                MediaPlayer next = nextPlayers.remove(channel);
                                if (next != null) next.release();
                                return;
                            }
                            applyVolume(owner, (int) (initial * (milliseconds - elapsed) / milliseconds), position);
                        }
                        android.os.SystemClock.sleep(10);
                    }
                }
            }, "majiro-audio-fade").start();
            return true;
        }
        throw new IllegalArgumentException("unknown audio operation: " + action);
    }

    private static void applyVolume(MediaPlayer player, int volume, int pan) {
        float level = Math.max(0, Math.min(1000, volume)) / 1000f;
        float balance = Math.max(-10000, Math.min(10000, pan)) / 10000f;
        player.setVolume(level * (balance > 0 ? 1 - balance : 1),
                        level * (balance < 0 ? 1 + balance : 1));
    }
    private static final HashMap<String, Integer> levels = new HashMap<String, Integer>();

    private static void observeAudio(final String channel, final MediaPlayer owner) {
        owner.setOnCompletionListener(new MediaPlayer.OnCompletionListener(){
            @Override public void onCompletion(MediaPlayer completed){
                synchronized(GameBridge.class){
                    if(players.get(channel)==owner)audioCompletion.put(channel,true);
                }
            }
        });
        owner.setOnErrorListener(new MediaPlayer.OnErrorListener() {
            @Override public boolean onError(MediaPlayer failed, int what, int extra) {
                synchronized (GameBridge.class) {
                    if (players.get(channel) == owner || nextPlayers.get(channel) == owner)
                        mediaError = "audio decoding failed on " + channel + ": " + what + "/" + extra;
                }
                return true;
            }
        });
    }

    private static void promoteAudio(String channel, MediaPlayer previous, MediaPlayer next) {
        players.put(channel, next);
        nextPlayers.remove(channel);
        audioLifecycle.transfer(previous, next);
        previous.release();
    }

    private static MediaPlayer currentAudio(String channel) {
        MediaPlayer current = players.get(channel), next = nextPlayers.get(channel);
        if (current != null && next != null && !current.isPlaying() && next.isPlaying()) {
            promoteAudio(channel, current, next);
            return next;
        }
        return current;
    }

    public static synchronized void pause() {
        MovieTextures.pause();
        modalDialogs.foreground(false); hideDialog();
        if (audioLifecycle.isBackground()) return;
        try {
            for (String channel : new ArrayList<String>(players.keySet())) currentAudio(channel);
            audioLifecycle.background();
            videoPaused = video != null && video.isPlaying();
            if (videoPaused) video.pause();
        } catch (RuntimeException error) { mediaError = "media suspension failed: " + error; close(); }
    }
    public static synchronized void resume() {
        MovieTextures.resume();
        modalDialogs.foreground(true); scheduleDialog();
        if (!audioLifecycle.isBackground()) return;
        try {
            audioLifecycle.foreground();
            if (videoPaused && video != null) video.start();
        } catch (RuntimeException error) { mediaError = "media resume failed: " + error; close(); }
        videoPaused = false;
    }
    public static synchronized void close() {
        GameWindowCommands.reset();
        if(clipboard!=null&&clipboardListener!=null)clipboard.removePrimaryClipChangedListener(clipboardListener);
        clipboardListener=null;
        modalDialogs.close(); hideDialog();
        for (MediaPlayer player : players.values()) player.release();
        for (MediaPlayer player : nextPlayers.values()) player.release();
        nextPlayers.clear(); levels.clear();
        players.clear(); mediaFiles.clear();audioCompletion.clear();audioSourcePaths.clear();
        audioLifecycle.reset();
        closeVideo();
    }
}
