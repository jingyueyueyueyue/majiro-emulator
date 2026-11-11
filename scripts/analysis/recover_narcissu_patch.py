"""Read-only decompilation of the user's original Narcissu translation DLL."""
from pathlib import Path
import argparse
import ctypes
import json
import os
import subprocess
import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "convert"))
from port_common import ROOT, archive_dir


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--all", action="store_true")
    args = parser.parse_args()
    folder = archive_dir("narcissu_patch_code")
    manifests = [ROOT / "localization/narcissu/narci_himeko/manifest.json"]
    if args.all:
        manifests = sorted((ROOT / "localization/narcissu").glob("*/manifest.json"))
    import hashlib
    sources = {}
    for path in manifests:
        manifest = json.loads(path.read_text(encoding="utf8"))
        for row in manifest["files"]:
            if row["name"].lower().endswith(".dll"):
                source = Path(row["path"])
                sources.setdefault(hashlib.sha256(source.read_bytes()).hexdigest(), source)
        if args.all:
            for source in Path(manifest["source_directory"]).glob("*.dll"):
                sources.setdefault(hashlib.sha256(source.read_bytes()).hexdigest(), source)
    inputs = []
    for digest, source in sources.items():
        local = folder / ("20261110_patch_" + digest[:12] + ".dll")
        local.write_bytes(source.read_bytes())
        inputs.append({"source": str(source), "sha256": digest, "local": str(local),
                       "output": str(folder / (local.stem + ".c"))})

    def short(path):
        buffer = ctypes.create_unicode_buffer(32768)
        if not ctypes.windll.kernel32.GetShortPathNameW(str(path), buffer, len(buffer)):
            raise OSError("short path unavailable")
        return buffer.value

    environment = dict(os.environ)
    environment["JAVA_HOME"] = "C:/Program Files/Eclipse Adoptium/jdk-17.0.18.8-hotspot"
    for entry in inputs:
        local, output = Path(entry["local"]), Path(entry["output"])
        command = ["C:/Users/jingyue/.codex/tools/ghidra_11.0.3_PUBLIC/support/analyzeHeadless.bat",
                   short(folder), "narcissu_patch_" + entry["sha256"][:12],
                   "-import", short(local), "-readOnly", "-scriptPath", short(ROOT / "scripts/analysis"),
                   "-postScript", "DecompileNarcissuPatch.java", short(folder) + "/" + output.name]
        with (folder / (local.stem + "_ghidra.txt")).open("wb") as log:
            result = subprocess.run(command, env=environment, stdout=log, stderr=subprocess.STDOUT,
                                    creationflags=subprocess.CREATE_NO_WINDOW)
        if result.returncode:
            raise RuntimeError("Narcissu patch decompilation failed: " + str(folder))
        print(output.relative_to(ROOT), flush=True)
    (folder / "20261110_inputs.json").write_text(json.dumps(inputs, indent=2, ensure_ascii=True) + "\n",
                                               encoding="utf8")


if __name__ == "__main__":
    main()
