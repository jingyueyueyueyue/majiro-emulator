"""Exercise production text converters under sustained load and strict errors."""
from pathlib import Path
import json
import subprocess
import sys

sys.path[:0] = [str(Path(__file__).resolve().parents[1] / "convert")]
from port_common import ROOT, archive_dir, read_json, sha256, timestamp, write_json


def main():
    folder = archive_dir("text_codec")
    java = Path(read_json(ROOT / "reports/android_toolchain.json")["java"]) / "bin"
    source = ROOT / "player/android/src/org/majiro/player/TextCodec.java"
    for name, command in (
        ("compile", [str(java / "javac.exe"), "-encoding", "UTF-8", "-d", str(folder),
                     str(source), str(ROOT / "scripts/analysis/TextCodecChecks.java")]),
        ("run", [str(java / "java.exe"), "-Xmx24m", "-cp", str(folder),
                 "org.majiro.player.TextCodecChecks"]),
    ):
        result = subprocess.run(command, capture_output=True, timeout=90)
        (folder / (name + ".txt")).write_bytes(result.stdout + result.stderr)
        assert result.returncode == 0, str(folder / (name + ".txt"))
    report = dict(json.loads(result.stdout), timestamp=timestamp(), source_sha256=sha256(source),
                  evidence=str(folder.relative_to(ROOT)), device_accessed=False,
                  scope="CP932/GBK/UTF-8 round trips, strict failures followed by recovery, four independent threads")
    write_json(folder / "report.json", report)
    write_json(ROOT / "reports/text_codec_tests.json", report)
    print(json.dumps(report))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
