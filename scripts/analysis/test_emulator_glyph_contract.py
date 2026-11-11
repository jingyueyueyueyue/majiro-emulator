"""Execute the production Java glyph boundary with deterministic font/raster stubs."""
from pathlib import Path
import json
import subprocess
import sys

from test_audio_preparation_cleanup import method
sys.path.insert(0, str(Path(__file__).resolve().parents[1]/"convert"))
from port_common import ROOT, read_json, sha256, timestamp, write_json


def run():
    production = ROOT/"player/android/src/org/majiro/player/GameBridge.java"
    body = method(production.read_text("utf8"), "public static int[] glyphs(")
    folder = ROOT.parent/"archive/2026-11/20261111_emulator_audit_host/glyph_contract"
    folder.mkdir(parents=True, exist_ok=True)
    source = r"""
import java.util.*;import java.io.*;
public class GameBridge{
 static int fontQualityMode=0;static Typeface customTypeface;
 static HashMap<String,Typeface> privateTypefaces=new HashMap<>();
 static class Context{File getCacheDir(){return new File(".");}}
 static Context context=new Context();
 static String key(String s){return s.toLowerCase();}
 static byte[] readFile(String s){return new byte[]{1};}
 static class Typeface{
  static final int NORMAL=0;boolean custom;
  static Typeface create(String n,int s){return new Typeface();}
  static Typeface createFromFile(File f){Typeface t=new Typeface();t.custom=true;return t;}
 }
 static class PatchTypography{
  static Typeface apply(String s,Typeface t){return t;}static String family(String s){return s;}
 }
 static class Paint{
  static final int ANTI_ALIAS_FLAG=1,SUBPIXEL_TEXT_FLAG=2;
  Typeface face;float size;Paint(int f){}
  void setTypeface(Typeface t){face=t;}void setTextSize(float s){size=s;}
  float measureText(String s){return face.custom?8:2;}
  void setColor(int c){}
 }
 static class Color{static final int WHITE=-1;}
 static class Bitmap{
  static int allocations;static class Config{static final Config ARGB_8888=new Config();}
  static Bitmap createBitmap(int w,int h,Config c){allocations++;return new Bitmap();}
  void getPixels(int[] d,int o,int st,int x,int y,int w,int h){}
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
  try{glyphs("face",10,new int[]{1},"AA",false);throw new AssertionError("short advances accepted");}
  catch(IllegalArgumentException expected){}
  check(Bitmap.allocations==allocations,"invalid advances allocated bitmap");
  fontQualityMode=1;
  try{glyphs("face",4096,new int[]{4096},"A",false);throw new AssertionError("supersample bound missing");}
  catch(IllegalStateException expected){}
  check(Bitmap.allocations==allocations,"oversized supersampling allocated bitmap");
  System.out.println("{\"status\":\"passed\",\"checks\":4}");
 }
}
"""
    file = folder/"GameBridge.java"
    file.write_text(source.replace("// METHOD", body), encoding="utf8")
    java = Path(read_json(ROOT/"reports/android_toolchain.json")["java"])/"bin"
    for index, command in enumerate((
        [str(java/"javac.exe"), "-encoding", "UTF-8", "-d", str(folder), str(file)],
        [str(java/"java.exe"), "-cp", str(folder), "GameBridge"],
    )):
        result = subprocess.run(command, cwd=folder, capture_output=True, timeout=30)
        (folder/f"20261111_{index}.txt").write_bytes(result.stdout+result.stderr)
        assert result.returncode == 0, result.stderr.decode("utf8", errors="replace")
    report = json.loads(result.stdout)
    report.update(timestamp=timestamp(), source_sha256=sha256(production), device_accessed=False,
                  scope="unchanged production glyph method, deterministic font/raster boundary")
    write_json(folder/"20261111_result.json", report)
    print(json.dumps(report))


if __name__ == "__main__":
    run()
