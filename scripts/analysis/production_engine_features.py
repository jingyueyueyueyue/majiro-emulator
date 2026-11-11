"""Run the actual Android-free production Java feature detector in PC fixtures."""
from functools import lru_cache
from pathlib import Path
import subprocess

from port_common import ROOT, archive_dir, read_json, sha256


@lru_cache(maxsize=4)
def _compiler(identity):
    folder = archive_dir("production_engine_feature_fixture")
    java = Path(read_json(ROOT / "reports/android_toolchain.json")["java"]) / "bin"
    source = ROOT / "player/android/src/org/majiro/player/EngineFeatures.java"
    fixture = ROOT / "scripts/analysis/EngineFeaturesChecks.java"
    result = subprocess.run([str(java / "javac.exe"), "-encoding", "UTF-8", "-d", str(folder),
                             str(source), str(fixture)], capture_output=True, timeout=60)
    (folder / "compile.txt").write_bytes(result.stdout + result.stderr)
    if result.returncode:
        raise RuntimeError("production engine feature fixture compilation failed: " + str(folder))
    assert sha256(source) == identity, "production feature source changed during fixture compilation"
    return folder, java / "java.exe"


@lru_cache(maxsize=128)
def _features(identity, inputs):
    folder, java = _compiler(identity)
    listing = folder / ("inputs_" + str(_features.cache_info().misses) + ".txt")
    listing.write_text("\n".join(path for path, size, modified in inputs) + "\n", encoding="utf8")
    result = subprocess.run([str(java), "-cp", str(folder),
                             "org.majiro.player.EngineFeaturesChecks", str(listing)],
                            capture_output=True, timeout=60)
    listing.with_suffix(".log").write_bytes(result.stdout + result.stderr)
    if result.returncode:
        raise RuntimeError("production engine feature fixture failed: " + str(listing))
    lines = result.stdout.decode("utf8").splitlines()
    assert len(lines) == len(inputs), "production feature output count differs"
    grouped = []
    for line in lines:
        values = [feature for feature in line.split(",") if feature]
        if values:
            grouped.extend(("engine-source:begin", *values, "engine-source:end"))
    return tuple(grouped)


def features(paths):
    inputs = tuple((str(path), path.stat().st_size, path.stat().st_mtime_ns)
                   for path in sorted(set(map(Path, paths)))
                   if path.stat().st_size <= 128 * 1024 * 1024)
    if not inputs:
        return []
    source = ROOT / "player/android/src/org/majiro/player/EngineFeatures.java"
    return list(_features(sha256(source), inputs))
