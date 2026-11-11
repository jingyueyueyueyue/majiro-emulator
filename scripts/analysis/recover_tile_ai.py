"""Read-only AI helper decompilation into the current dated evidence archive."""
from pathlib import Path
import ctypes
import os
import subprocess
import sys
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/"convert"))
from port_common import ROOT,archive_dir

def main():
    folder=archive_dir("tile_ai")
    project=ROOT/"archive/2026-11/20261110_120139_810374_tile_decompilation"
    def short(path):
        buffer=ctypes.create_unicode_buffer(32768)
        if not ctypes.windll.kernel32.GetShortPathNameW(str(path),buffer,len(buffer)):
            raise OSError("short path unavailable")
        return buffer.value
    environment=dict(os.environ)
    environment["JAVA_HOME"]="C:/Program Files/Eclipse Adoptium/jdk-17.0.18.8-hotspot"
    command=["C:/Users/jingyue/.codex/tools/ghidra_11.0.3_PUBLIC/support/analyzeHeadless.bat",
        short(project),"majiro_tiles","-process","20261110_original_reference.exe","-noanalysis",
        "-readOnly","-scriptPath",short(ROOT/"scripts/analysis"),"-postScript",
        "ExportTileInstructions.java"if"--instructions"in sys.argv else"DecompileTileAI.java",
        short(folder)+"/20261110_ai.c"]
    with(folder/"20261110_ghidra.txt").open("wb")as output:
        result=subprocess.run(command,env=environment,stdout=output,stderr=subprocess.STDOUT,
            creationflags=subprocess.CREATE_NO_WINDOW)
    if result.returncode:raise RuntimeError("tile AI decompilation failed: "+str(folder))
    print(folder.relative_to(ROOT))

if __name__=="__main__":main()
