"""Measure feature discovery on preloaded original EXEs; compare exact outputs."""
from pathlib import Path
import argparse
import json
import statistics
import subprocess
import sys

sys.path[:0] = [str(Path(__file__).resolve().parents[1] / "convert")]
from port_common import ROOT, read_json, sha256, timestamp, write_json


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--baseline", type=Path, required=True)
    parser.add_argument("--rounds", type=int, default=9)
    args = parser.parse_args()
    folder = ROOT / "archive/2026-11/20261111_emulator_audit_profiles/20261111_phase3" / (
        timestamp().replace(":", "").replace("+", "_") + "_benchmark")
    folder.mkdir(parents=True, exist_ok=False)
    source = ROOT / "player/android/src/org/majiro/player/EngineFeatures.java"
    java = Path(read_json(ROOT / "reports/android_toolchain.json")["java"]) / "bin"
    samples = read_json(ROOT / "reports/opcode_dispatch.json")["samples"]
    wrapper = read_json(ROOT / "reports/happy_wrapper_recovery.json")
    paths = [s["path"] for s in samples] + [wrapper["original_container"], wrapper["initialized_reference"]]
    listing = folder / "20261111_inputs.txt"
    listing.write_text("\n".join(paths) + "\n", encoding="utf8")
    fixture = folder / "EngineFeaturesBenchmark.java"
    fixture.write_text("""package org.majiro.player;
import java.nio.file.*;import java.nio.charset.StandardCharsets;import java.util.*;
public final class EngineFeaturesBenchmark {
  public static void main(String[]args)throws Exception {
    List<byte[]> inputs=new ArrayList<byte[]>();
    for(String path:Files.readAllLines(Paths.get(args[0]),StandardCharsets.UTF_8))
      inputs.add(Files.readAllBytes(Paths.get(path)));
    for(byte[] input:inputs)System.out.println(String.join(",",EngineFeatures.from(input)));
    long consume=0;
    for(int warm=0;warm<5;++warm)for(byte[] input:inputs)consume+=EngineFeatures.from(input).length;
    for(int round=0;round<Integer.parseInt(args[1]);++round){
      long start=System.nanoTime();
      for(byte[] input:inputs)consume+=EngineFeatures.from(input).length;
      System.out.println("NS "+(System.nanoTime()-start));
    }
    System.out.println("CONSUME "+consume);
  }
}
""", encoding="utf8")
    results = {}
    for name, path in (("before", args.baseline), ("after", source)):
        out = folder / name
        out.mkdir()
        copied = out / "EngineFeatures.java"
        copied.write_bytes(path.read_bytes())
        result = subprocess.run([str(java / "javac.exe"), "-encoding", "UTF-8", "-d", str(out),
                                 str(copied), str(fixture)], capture_output=True, timeout=60)
        (out / "20261111_compile.log").write_bytes(result.stdout + result.stderr)
        assert result.returncode == 0, result.stderr
        result = subprocess.run([str(java / "java.exe"), "-Xms64m", "-Xmx256m", "-cp", str(out),
                                 "org.majiro.player.EngineFeaturesBenchmark", str(listing), str(args.rounds)],
                                capture_output=True, timeout=120)
        (out / "20261111_measurement.log").write_bytes(result.stdout + result.stderr)
        assert result.returncode == 0, result.stderr
        lines = result.stdout.decode("utf8").splitlines()
        timings = [int(line[3:]) / 1000000 for line in lines if line.startswith("NS ")]
        assert len(timings) == args.rounds
        results[name] = {"source_sha256": sha256(path), "features": lines[:len(paths)],
                         "round_ms": timings, "median_suite_ms": statistics.median(timings)}
    assert results["before"]["features"] == results["after"]["features"]
    report = {"timestamp": timestamp(), "status": "passed", "inputs": len(paths), "rounds": args.rounds,
              "preloaded_inputs": True, "file_reads_excluded_from_timing": True,
              "outputs_exactly_equal": True, "measurements": results,
              "median_reduction_percent": (1 - results["after"]["median_suite_ms"] /
                                            results["before"]["median_suite_ms"]) * 100,
              "scope": "Windows JVM detector-only measurement; Android startup latency is not claimed.",
              "evidence": str(folder.relative_to(ROOT)), "device_accessed": False,
              "persistent_cache_introduced": False}
    write_json(folder / "20261111_report.json", report)
    write_json(ROOT / "reports/engine_features_performance.json", report)
    print(json.dumps({k: report[k] for k in ("status", "inputs", "rounds", "median_reduction_percent")}))
    for name, data in results.items():
        print(name, "median suite ms", round(data["median_suite_ms"], 3))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
