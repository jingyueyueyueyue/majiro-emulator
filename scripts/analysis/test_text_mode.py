"""Verify automatic local-first mode selection with the production manifest and selector."""
from pathlib import Path
import json
import os
import subprocess
import sys
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/"convert"))
from port_common import ROOT,archive_dir,read_json,sha256,timestamp,write_json


def main():
    folder=archive_dir("automatic_text_mode")
    java=Path(read_json(ROOT/"reports/android_toolchain.json")["java"])/"bin"
    jar=ROOT/"references/player/testing-json/json-20240303.jar"
    sources=[ROOT/"player/android/src/org/majiro/player"/name for name in("TextMode.java","PatchManifest.java")]
    result=subprocess.run([str(java/"javac.exe"),"-encoding","UTF-8","-cp",str(jar),"-d",str(folder),
        *map(str,sources),str(ROOT/"scripts/analysis/TextModeChecks.java")],capture_output=True,timeout=30)
    (folder/"compile.txt").write_bytes(result.stdout+result.stderr)
    if result.returncode:raise RuntimeError("text mode compile: "+str(folder/"compile.txt"))
    result=subprocess.run([str(java/"java.exe"),"-cp",os.pathsep.join(map(str,(folder,jar))),
        "org.majiro.player.TextModeChecks"],capture_output=True,timeout=10)
    (folder/"run.txt").write_bytes(result.stdout+result.stderr)
    if result.returncode:raise RuntimeError("text mode failure: "+str(folder/"run.txt"))
    report=json.loads(result.stdout.decode("utf8"))
    settings=(ROOT/"player/android/src/org/majiro/player/TranslationSettings.java").read_text(encoding="utf8")
    bridge=(ROOT/"player/android/src/org/majiro/player/GameBridge.java").read_text(encoding="utf8")
    assert 'getString("mode","auto")'in settings
    assert 'TextMode.resolve(requestedTextMode,candidate' in bridge
    assert bridge.index("TextMode.resolve(")<bridge.index("String textMode=launchTextMode")
    report.update(timestamp=timestamp(),default_preference="auto",device_accessed=False,
        source_sha256={p.name:sha256(p)for p in sources},evidence=str(folder.relative_to(ROOT)))
    write_json(ROOT/"reports/automatic_text_mode_tests.json",report)
    print(json.dumps(report,ensure_ascii=False))
    return 0


if __name__=="__main__":raise SystemExit(main())
