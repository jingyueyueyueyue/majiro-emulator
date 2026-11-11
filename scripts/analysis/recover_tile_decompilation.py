"""Create an archived headless Ghidra analysis of original pure tile routines."""
from pathlib import Path
import ctypes
import os
import subprocess
import sys

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/"convert"))
from port_common import ROOT,archive_dir


def main():
    folder=archive_dir("tile_decompilation")
    ghidra=Path("C:/Users/jingyue/.codex/tools/ghidra_11.0.3_PUBLIC/support/analyzeHeadless.bat")
    executable=next(path for path in (ROOT/"bin").glob("*.exe")if path.name!="uninst.exe")
    alias=folder/"20261110_original_reference.exe"
    os.link(executable,alias)
    env=dict(os.environ)
    env["JAVA_HOME"]="C:/Program Files/Eclipse Adoptium/jdk-17.0.18.8-hotspot"
    def short(path):
        buffer=ctypes.create_unicode_buffer(32768)
        if not ctypes.windll.kernel32.GetShortPathNameW(str(path),buffer,len(buffer)):
            return str(path)
        return buffer.value
    command=[str(ghidra),short(folder),"majiro_tiles","-import",short(alias),
             "-analysisTimeoutPerFile","90","-scriptPath",str(ROOT/"scripts/analysis"),
             "-postScript","DecompileMajiroTiles.java",str(folder/"20261110_tiles_decompiled.c")]
    command[command.index("-scriptPath")+1]=short(ROOT/"scripts/analysis")
    command[-1]=short(folder)+"/20261110_tiles_decompiled.c"
    print(folder,flush=True)
    with (folder/"20261110_ghidra.txt").open("wb")as log:
        process=subprocess.Popen(command,stdout=log,stderr=subprocess.STDOUT,env=env,
                                 creationflags=subprocess.CREATE_NO_WINDOW)
        result=process.wait()
    alias.unlink()
    if result:raise RuntimeError("headless tile analysis failed: "+str(folder))
    print("tile decompilation archived",folder,flush=True)


if __name__=="__main__":main()
