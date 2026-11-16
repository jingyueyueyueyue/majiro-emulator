"""Check original Windows family aliases and optional user-owned TTC selection."""
from pathlib import Path
import json
import subprocess
import sys

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/"convert"))
from port_common import ROOT,archive_dir,read_json,sha256,timestamp,write_json


def main():
    folder=archive_dir("original_typefaces")
    sources={
        "android/content/Context.java":"""package android.content;import java.io.File;
public class Context{final File root;public Context(File f){root=f;}public File getFilesDir(){return root;}}""",
        "android/graphics/Typeface.java":"""package android.graphics;import java.io.File;
public class Typeface{public int index;public static int builds;
public static class Builder{int index;public Builder(File f){}
 public Builder setTtcIndex(int i){index=i;return this;}
 public Typeface build(){Typeface f=new Typeface();f.index=index;builds++;return f;}}}""",
        "org/majiro/player/OriginalTypefaceChecks.java":r"""
package org.majiro.player;
import java.io.*;import android.content.Context;import android.graphics.Typeface;
public class OriginalTypefaceChecks{
 public static void main(String[] args)throws Exception{
  String[] aliases={"MS Gothic","\uff2d\uff33 \u30b4\u30b7\u30c3\u30af","MS UI Gothic",
     "MS PGothic","\uff2d\uff33 \uff30\u30b4\u30b7\u30c3\u30af","Source Han Sans SC","serif"};
  int[] expected={0,0,1,2,2,-1,-1};
  for(int i=0;i<aliases.length;i++)
   if(OriginalTypefaces.gothicIndex(aliases[i])!=expected[i])throw new AssertionError(aliases[i]);
  File root=new File(args[0]);Context context=new Context(root);
  if(OriginalTypefaces.resolve(context,"MS Gothic")!=null)throw new AssertionError("missing private font");
  File fonts=new File(root,"fonts");fonts.mkdirs();new File(fonts,"msgothic.ttc").createNewFile();
  OriginalTypefaces.reset();
  Typeface proportional=OriginalTypefaces.resolve(context,aliases[4]);
  if(proportional.index!=2)throw new AssertionError("wrong TTC face");
  if(proportional!=OriginalTypefaces.resolve(context,"MS PGothic"))
   throw new AssertionError("alias missed cache");
  if(OriginalTypefaces.resolve(context,"MS UI Gothic").index!=1)throw new AssertionError("UI Gothic");
  if(OriginalTypefaces.resolve(context,"MS Gothic").index!=0||Typeface.builds!=3)
   throw new AssertionError("Gothic cache isolation");
  System.out.println("{\"checks\":12}");
 }
}
"""}
    files=[]
    for name,text in sources.items():
        path=folder/name;path.parent.mkdir(parents=True,exist_ok=True)
        path.write_text(text,encoding="utf8");files.append(str(path))
    production=ROOT/"player/android/src/org/majiro/player/OriginalTypefaces.java"
    files.append(str(production))
    java=Path(read_json(ROOT/"reports/android_toolchain.json")["java"])/"bin"
    for name,command in(
        ("compile",[str(java/"javac.exe"),"-encoding","UTF-8","-d",str(folder),*files]),
        ("run",[str(java/"java.exe"),"-cp",str(folder),"org.majiro.player.OriginalTypefaceChecks",
                str(folder/"files")])):
        result=subprocess.run(command,cwd=folder,capture_output=True,timeout=30)
        (folder/(name+".txt")).write_bytes(result.stdout+result.stderr)
        if result.returncode:raise RuntimeError(name+" failed: "+str(folder))
    report=json.loads(result.stdout)
    report.update(status="passed",timestamp=timestamp(),device_accessed=False,
                  source_sha256=sha256(production),evidence=str(folder.relative_to(ROOT)))
    write_json(folder/"report.json",report);write_json(ROOT/"reports/original_typefaces_tests.json",report)
    print(json.dumps(report))
    return 0


if __name__=="__main__":raise SystemExit(main())
