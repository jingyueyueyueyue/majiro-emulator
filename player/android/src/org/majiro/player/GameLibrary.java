package org.majiro.player;

import android.content.Context;
import android.database.Cursor;
import android.graphics.Bitmap;
import android.net.Uri;
import android.provider.DocumentsContract;
import org.json.JSONArray;
import org.json.JSONObject;
import java.io.File;
import java.io.FileOutputStream;
import java.io.InputStream;
import java.nio.charset.StandardCharsets;
import java.security.MessageDigest;
import java.util.ArrayList;
import java.util.Collections;
import java.util.Comparator;
import java.util.LinkedHashSet;
import java.util.Locale;

/** Persistent authorized roots, separate from game data and player saves. */
public final class GameLibrary {
    public static final class Entry {
        public String id,tree,root,title,icon,formats;
        public long lastPlayed;
        JSONObject json() throws Exception {
            return new JSONObject().put("id",id).put("tree",tree).put("root",root).put("title",title)
                .put("icon",icon).put("formats",formats).put("lastPlayed",lastPlayed);
        }
    }
    private static final class Document {String id,name,mime;long bytes;}
    private final Context context;
    private final ArrayList<Entry> entries=new ArrayList<Entry>();
    public GameLibrary(Context context) {
        this.context=context;
        try {
            JSONArray list=new JSONArray(context.getSharedPreferences("library",0).getString("games","[]"));
            for(int i=0;i<list.length();++i) {
                JSONObject row=list.getJSONObject(i);Entry e=new Entry();
                e.id=row.getString("id");e.tree=row.getString("tree");e.root=row.getString("root");
                e.title=row.getString("title");e.icon=row.optString("icon","");
                e.formats=row.optString("formats","");e.lastPlayed=row.optLong("lastPlayed");entries.add(e);
            }
        }catch(Exception error){DiagnosticLog.write("library","load failed: "+error);}
    }
    public synchronized ArrayList<Entry> all() {
        ArrayList<Entry> result=new ArrayList<Entry>(entries);
        Collections.sort(result,(a,b)->a.lastPlayed==b.lastPlayed?a.title.compareToIgnoreCase(b.title):Long.compare(b.lastPlayed,a.lastPlayed));
        return result;
    }
    private synchronized void save() throws Exception {
        JSONArray list=new JSONArray();for(Entry e:entries)list.put(e.json());
        if(!context.getSharedPreferences("library",0).edit().putString("games",list.toString()).commit())
            throw new IllegalStateException("无法保存游戏库");
    }
    public synchronized void played(Entry e) throws Exception {e.lastPlayed=System.currentTimeMillis();save();}
    public synchronized void rename(Entry e,String name) throws Exception {
        if(name.trim().isEmpty())throw new IllegalArgumentException("名称不能为空");
        e.title=name.trim();save();
    }
    public synchronized void remove(Entry e) throws Exception {entries.remove(e);save();}
    private ArrayList<Document> children(Uri tree,String id) throws Exception {
        ArrayList<Document> result=new ArrayList<Document>();
        try(Cursor cursor=context.getContentResolver().query(
            DocumentsContract.buildChildDocumentsUriUsingTree(tree,id),new String[]{
                DocumentsContract.Document.COLUMN_DOCUMENT_ID,DocumentsContract.Document.COLUMN_DISPLAY_NAME,
                DocumentsContract.Document.COLUMN_MIME_TYPE,DocumentsContract.Document.COLUMN_SIZE},null,null,null)) {
            if(cursor==null)throw new IllegalStateException("无法读取文件夹");
            while(cursor.moveToNext()) {
                if(result.size()>=20000)throw new IllegalStateException("文件夹项目过多");
                Document d=new Document();d.id=cursor.getString(0);d.name=cursor.getString(1);
                d.mime=cursor.getString(2);d.bytes=cursor.isNull(3)?0:cursor.getLong(3);result.add(d);
            }
        }
        return result;
    }
    private static String hash(String value) throws Exception {
        byte[] raw=MessageDigest.getInstance("SHA-256").digest(value.getBytes(StandardCharsets.UTF_8));
        StringBuilder out=new StringBuilder();for(int i=0;i<12;++i)out.append(String.format(Locale.ROOT,"%02x",raw[i]&255));
        return out.toString();
    }
    public ArrayList<Entry> addTree(Uri tree) throws Exception {
        ArrayList<Entry> found=new ArrayList<Entry>();
        discover(tree,DocumentsContract.getTreeDocumentId(tree),0,found,new int[]{0});
        if(found.isEmpty())throw new IllegalArgumentException("没有找到游戏资源，请选择游戏文件夹或存放多个游戏的目录。");
        synchronized(this) {
            for(Entry e:found) {
                Entry old=null;for(Entry value:entries)if(value.id.equals(e.id)){old=value;break;}
                if(old==null)entries.add(e);
                else {old.tree=e.tree;old.formats=e.formats;if(!e.icon.isEmpty())old.icon=e.icon;}
            }
            save();
        }
        return found;
    }
    private void discover(Uri tree,String id,int depth,ArrayList<Entry> result,int[] visited) throws Exception {
        if(++visited[0]>200||result.size()>=100)throw new IllegalStateException("一次最多添加 100 个游戏");
        ArrayList<Document> documents=children(tree,id);boolean archives=false;
        LinkedHashSet<String> versions=new LinkedHashSet<String>();
        for(Document d:documents) {
            String name=d.name.toLowerCase(Locale.ROOT);
            if(!name.endsWith(".arc")||name.equals("savedata.arc"))continue;
            try(InputStream input=context.getContentResolver().openInputStream(DocumentsContract.buildDocumentUriUsingTree(tree,d.id))) {
                if(input==null)continue;byte[] bytes=new byte[16];int at=0,n;
                while(at<16&&(n=input.read(bytes,at,16-at))>0)at+=n;
                String header=new String(bytes,StandardCharsets.US_ASCII);
                if(at>=15&&(header.startsWith("MajiroArcV1.000\0")||header.startsWith("MajiroArcV2.000\0")||header.startsWith("MajiroArcV3.000\0"))) {
                    archives=true;versions.add("V"+header.charAt(10));
                }
            }
        }
        if(archives) {
            Entry e=new Entry();e.id=hash(tree.getAuthority()+"\n"+id);e.tree=tree.toString();e.root=id;
            e.title=id.substring(id.lastIndexOf('/')+1);
            if(e.title.indexOf(':')>=0)e.title=e.title.substring(e.title.lastIndexOf(':')+1);
            e.formats=android.text.TextUtils.join(" / ",versions);e.icon="";
            for(Document d:documents)if(d.name.equalsIgnoreCase("majiro-game.json")) {
                try(InputStream input=context.getContentResolver().openInputStream(DocumentsContract.buildDocumentUriUsingTree(tree,d.id))) {
                    if(input!=null) {byte[] bytes=new byte[8192];int at=0,n;
                        while(at<bytes.length&&(n=input.read(bytes,at,bytes.length-at))>0)at+=n;
                        if(at>0)e.title=new JSONObject(new String(bytes,0,at,StandardCharsets.UTF_8)).optString("title",e.title);}
                }
            }
            if(e.title==null||e.title.trim().isEmpty())e.title="未命名游戏";
            ArrayList<Document> icons=new ArrayList<Document>();
            for(Document d:documents) {
                if(d.name.equalsIgnoreCase("bin")&&DocumentsContract.Document.MIME_TYPE_DIR.equals(d.mime))icons.addAll(children(tree,d.id));
                else icons.add(d);
            }
            Collections.sort(icons,(a,b)->Long.compare(b.bytes,a.bytes));
            for(Document d:icons) {
                String name=d.name.toLowerCase(Locale.ROOT);
                if(!name.endsWith(".exe")||name.contains("uninst")||name.contains("menu")||name.equals("install.exe")||name.equals("autorun.exe"))continue;
                try {
                    Bitmap bitmap=GameIcon.load(context.getContentResolver(),DocumentsContract.buildDocumentUriUsingTree(tree,d.id));
                    if(bitmap==null)continue;
                    File folder=new File(context.getFilesDir(),"library-icons");
                    if(!folder.isDirectory()&&!folder.mkdirs())throw new IllegalStateException("图标缓存目录不可用");
                    File file=new File(folder,e.id+".png");
                    try(FileOutputStream out=new FileOutputStream(file)){bitmap.compress(Bitmap.CompressFormat.PNG,100,out);}
                    e.icon=file.getAbsolutePath();bitmap.recycle();break;
                }catch(Exception error){DiagnosticLog.write("icon",d.name+": "+error);}
            }
            result.add(e);DiagnosticLog.write("library","identified "+e.id+" "+e.title+" "+e.formats);return;
        }
        if(depth>=3)return;
        for(Document d:documents)if(DocumentsContract.Document.MIME_TYPE_DIR.equals(d.mime)) {
            String name=d.name.toLowerCase(Locale.ROOT);
            if(name.equals("savedata")||name.equals("save_chs")||name.equals("archive")||name.equals("movie"))continue;
            discover(tree,d.id,depth+1,result,visited);
        }
    }
}
