"""Execute the production Java glyph boundary with deterministic font/raster stubs."""
from pathlib import Path
import json
import subprocess
import sys

from test_audio_preparation_cleanup import method
sys.path.insert(0, str(Path(__file__).resolve().parents[1]/"convert"))
from port_common import ROOT, archive_dir, read_json, sha256, timestamp, write_json


def run():
    production = ROOT/"player/android/src/org/majiro/player/GameBridge.java"
    body = method(production.read_text("utf8"), "public static int[] glyphs(")
    folder = archive_dir("glyph_contract")
    source = r"""
package org.majiro.player;
import java.util.*;import java.io.*;import java.nio.*;
public class GameBridge{
 static int fontQualityMode=0;static Typeface customTypeface;static boolean patchTypefaceChecked;
 static int fileReads;static boolean fontPresent=true;
 static String mode="local";static String translationMode(){return mode;}
 static HashMap<String,Typeface> privateTypefaces=new HashMap<>();
 static GlyphCache glyphCache=new GlyphCache(32,8L*1024*1024);
 static class Context{File getCacheDir(){return new File(".");}}
 static Context context=new Context();
 static String key(String s){return s.toLowerCase();}
 static byte[] readFile(String s){fileReads++;return fontPresent?new byte[]{1}:null;}
 static class Typeface{
  static final int NORMAL=0;boolean custom;
  static Typeface create(String n,int s){return new Typeface();}
  static Typeface createFromFile(File f){Typeface t=new Typeface();t.custom=true;return t;}
 }
 static class PatchTypography{
  static Typeface apply(String s,Typeface t){return t;}
  static String family(String s){return s.substring(s.lastIndexOf(':')+1);}
 }
 static class OriginalTypefaces{
  static Typeface resolve(Context c,String f){return null;}
 }
 static class Paint{
  static final int ANTI_ALIAS_FLAG=1,SUBPIXEL_TEXT_FLAG=2;
  Typeface face;float size;Paint(int f){}
  void setTypeface(Typeface t){face=t;}void setTextSize(float s){size=s;}
  void setTextLocale(Locale l){}
  float measureText(String s){return face.custom?8:2;}
  void setColor(int c){}
 }
 static class Color{static final int WHITE=-1;}
 static class Bitmap{
  static int allocations;static class Config{static final Config ARGB_8888=new Config();static final Config ALPHA_8=new Config();}
  static Bitmap createBitmap(int w,int h,Config c){allocations++;return new Bitmap();}
  void getPixels(int[] d,int o,int st,int x,int y,int w,int h){}
  int getRowBytes(){return 64;}void copyPixelsToBuffer(ByteBuffer b){}
  void recycle(){}
 }
 static class Canvas{
  Canvas(Bitmap b){}void save(){}void translate(float x,float y){}void scale(float x,float y){}
  void drawText(String s,float x,float y,Paint p){}void restore(){}
 }
 static class GlyphCell{
  float baseline=8,verticalScale=1;
  static GlyphCell measure(Paint p,int[] c,int h,int q){return new GlyphCell();}
 }
 // METHOD
 static void check(boolean b,String why){if(!b)throw new AssertionError(why);}
 public static void main(String[]args){
  int[] first=glyphs("face",10,new int[]{0,10},"AA",true);
  check(first[0]==8&&first[1]==20,"first layout measured a different font");
  int[] second=glyphs("face",10,new int[]{0,10},"AA",true);
  check(Arrays.equals(first,second),"first frame differs from subsequent layout");
  int allocations=Bitmap.allocations;
  check(first==second,"identical font/layout missed cache");
  customTypeface=new Typeface();
  int[] changed=glyphs("face",10,new int[]{0,10},"AA",true);
  check(changed[0]==4&&changed!=first,"different resolved font reused stale glyphs");
  Typeface registered=new Typeface();registered.custom=true;privateTypefaces.put("private",registered);
  check(glyphs("style:private",10,new int[]{0,10},"AA",true)[0]==8,
        "style prefix hid registered private family");
  customTypeface=null;fontPresent=false;patchTypefaceChecked=false;fileReads=0;glyphCache.clear();
  glyphs("face",10,new int[]{10},"A",false);
  glyphs("face",10,new int[]{10},"B",false);
  check(fileReads==1,"absent patch font queried more than once");
  customTypeface=null;mode="original";fileReads=0;patchTypefaceChecked=false;glyphCache.clear();
  glyphs("face",10,new int[]{10},"A",false);
  check(fileReads==0,"original mode loaded a translation font");
  allocations=Bitmap.allocations;
  try{glyphs("face",10,new int[]{1},"AA",false);throw new AssertionError("short advances accepted");}
  catch(IllegalArgumentException expected){}
  check(Bitmap.allocations==allocations,"invalid advances allocated bitmap");
  try{glyphs("face",10,new int[]{Integer.MAX_VALUE,1},"AA",false);
      throw new AssertionError("overflowed advances accepted");}
  catch(IllegalStateException expected){}
  try{glyphs("face",10,new int[]{-1},"A",false);throw new AssertionError("negative advance accepted");}
  catch(IllegalArgumentException expected){}
  check(Bitmap.allocations==allocations,"invalid width allocated bitmap");
  fontQualityMode=1;
  try{glyphs("face",4096,new int[]{4096},"A",false);throw new AssertionError("supersample bound missing");}
  catch(IllegalStateException expected){}
  check(Bitmap.allocations==allocations,"oversized supersampling allocated bitmap");
  Object face1=new Object(),face2=new Object();
  GlyphCache bounded=new GlyphCache(2,36);
  int[] mask=new int[]{1,2,3};
  bounded.put(face1,"a",mask);bounded.put(face1,"b",mask);
  check(bounded.bytes()==28&&bounded.size()==2,"cache cost accounting");
  check(bounded.get(face2,"a")==null,"font identity ignored");
  bounded.get(face1,"a");bounded.put(face1,"c",mask);
  check(bounded.get(face1,"b")==null&&bounded.get(face1,"a")==mask,"LRU eviction");
  bounded.put(face1,"a",new int[6]);
  check(bounded.size()==1&&bounded.bytes()==26,"replacement budget");
  bounded.put(face1,"large",new int[100]);
  check(bounded.bytes()==26&&bounded.size()==1,"oversized raster retained or flushed useful cache");
  bounded.clear();check(bounded.bytes()==0&&bounded.size()==0,"cache reset");
  System.out.println("{\"status\":\"passed\",\"checks\":17}");
 }
}
"""
    file = folder/"GameBridge.java"
    file.write_text(source.replace("// METHOD", body), encoding="utf8")
    java = Path(read_json(ROOT/"reports/android_toolchain.json")["java"])/"bin"
    for index, command in enumerate((
        [str(java/"javac.exe"), "-encoding", "UTF-8", "-d", str(folder), str(file),
         str(ROOT/"player/android/src/org/majiro/player/MediaCache.java"),
         str(ROOT/"player/android/src/org/majiro/player/GlyphCache.java")],
        [str(java/"java.exe"), "-cp", str(folder), "org.majiro.player.GameBridge"],
    )):
        result = subprocess.run(command, cwd=folder, capture_output=True, timeout=30)
        (folder/f"{folder.name[:8]}_{index}.txt").write_bytes(result.stdout+result.stderr)
        assert result.returncode == 0, result.stderr.decode("utf8", errors="replace")
    report = json.loads(result.stdout)
    report.update(timestamp=timestamp(), source_sha256=sha256(production), device_accessed=False,
                  cache_sha256=sha256(ROOT/"player/android/src/org/majiro/player/GlyphCache.java"),
                  scope="unchanged production glyph method, deterministic font/raster boundary")
    write_json(folder/"report.json", report)
    write_json(ROOT/"reports/emulator_glyph_contract.json", report)
    print(json.dumps(report))


if __name__ == "__main__":
    run()
