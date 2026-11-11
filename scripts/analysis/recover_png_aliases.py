"""Read-only original PNG image/underscore alias decompilation."""
from pathlib import Path
import ctypes
import json
import os
import subprocess
import sys
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/"convert"))
from port_common import ROOT,archive_dir


def main():
    folder=archive_dir("original_png_aliases")
    sample=next(r for r in json.loads((ROOT/"reports/opcode_dispatch.json").read_text(encoding="utf8"))["samples"]
                if r["sample"]=="narci_onetwo")
    local=folder/"20261110_original_reference.exe"
    local.write_bytes(Path(sample["path"]).read_bytes())
    def short(path):
        buffer=ctypes.create_unicode_buffer(32768)
        if not ctypes.windll.kernel32.GetShortPathNameW(str(path),buffer,len(buffer)):
            raise OSError("short path unavailable")
        return buffer.value
    environment=dict(os.environ)
    environment["JAVA_HOME"]="C:/Program Files/Eclipse Adoptium/jdk-17.0.18.8-hotspot"
    command=["C:/Users/jingyue/.codex/tools/ghidra_11.0.3_PUBLIC/support/analyzeHeadless.bat",
             short(folder),"original_png","-import",short(local),"-readOnly","-scriptPath",
             short(ROOT/"scripts/analysis"),"-postScript","DecompilePngAliases.java",
             short(folder)+"/20261110_png_aliases.c"]
    with(folder/"20261110_ghidra.txt").open("wb")as log:
        result=subprocess.run(command,env=environment,stdout=log,stderr=subprocess.STDOUT,
                              creationflags=subprocess.CREATE_NO_WINDOW)
    local.unlink()
    if result.returncode:raise RuntimeError("PNG decompilation failed: "+str(folder))
    print(folder.relative_to(ROOT),flush=True)


if __name__=="__main__":main()
