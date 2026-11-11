"""Reproducible compatibility baseline for the independent player project."""
from __future__ import annotations
import argparse
import hashlib
import json
from pathlib import Path
import subprocess
import sys
import time

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "convert"))
from port_common import ROOT, archive_dir, read_json, timestamp, write_json


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--samples", type=Path, default=ROOT / "config/player_samples.json")
    parser.add_argument("--android", action="store_true", help="also compile the ARM64 core, SDL host and debug APK")
    parser.add_argument("--require-semantic-completion", action="store_true",
                        help="fail acceptance while any engine semantics remain unverified")
    args = parser.parse_args()
    samples = args.samples.resolve()
    if not samples.is_file():
        parser.error("sample manifest missing: " + str(samples))
    evidence = archive_dir("player_baseline")
    commands = [
        ("formats", ["scripts/analysis/test_formats.py"]),
        ("image_regression", ["scripts/analysis/test_images.py"]),
        ("samples", ["scripts/analysis/scan_majiro_samples.py",
                     "--samples", str(samples), "--depth", "0"]),
        ("compare", ["scripts/analysis/compare_majiro_samples.py"]),
        ("opcode_dispatch", ["scripts/analysis/recover_majiro_dispatch.py"]),
        ("syscall_dispatch", ["scripts/analysis/recover_majiro_syscalls.py"]),
        ("encrypted_images", ["scripts/analysis/test_encrypted_images.py", "--samples", str(samples)]),
        ("version_research", ["scripts/analysis/research_majiro_versions.py"]),
        ("native_core", ["scripts/build/build_player_core.py", *(["--android"] if args.android else [])]),
        ("native_core_tests", ["scripts/analysis/test_player_core.py"]),
        ("native_image_tests", ["scripts/analysis/test_player_images.py"]),
        ("raw_patch_loader", ["scripts/analysis/test_raw_player_patch.py"]),
        ("patch_manifest", ["scripts/analysis/test_patch_manifest.py"]),
        ("archive_layers", ["scripts/analysis/test_archive_layers.py"]),
        ("configuration_quotes", ["scripts/analysis/test_configuration_quotes.py"]),
        ("game_icons", ["scripts/analysis/test_game_icons.py"]),
        ("online_translation", ["scripts/analysis/test_online_translation.py"]),
        ("engine_semantics", ["scripts/build/run_player_semantics.py"]),
        ("story_entry_regressions", ["scripts/analysis/test_story_entry_regressions.py","--pages","30","--seconds","25"]),
        ("chapter_entry_regressions", ["scripts/analysis/test_chapter_regressions.py","--pages","3","--seconds","1.5","--jobs","3"]),
    ]
    if args.android:
        commands.extend([
            ("player_dependencies", ["scripts/build/fetch_player_dependencies.py"]),
            ("player_media", ["scripts/build/fetch_android_media.py"]),
            ("android_game", ["scripts/build/build_android_game.py"]),
            ("android_apk", ["scripts/build/build_android_probe.py"]),
        ])
    report = {
        "schema": 1, "timestamp": timestamp(), "stages": [],
        "sample_manifest_sha256": hashlib.sha256(samples.read_bytes()).hexdigest(),
        "phase": "bulk_semantics_before_device_acceptance", "complete": False,
        "primary_target": "android-arm64", "pc_role": "debug_and_behavior_reference",
        "release_allowed": False, "standalone_host": "shared_vm_sdl_host_source_present",
        "original_save_import": "not_implemented", "android": "not_built_by_this_run",
        "android_game_execution": "not_tested_by_this_run",
        "semantic_gate": "not_evaluated", "device_testing_allowed": False,
    }
    for name, command in commands:
        log = evidence / f"{evidence.name[:8]}_{name}.txt"
        began = time.monotonic()
        with log.open("wb") as output:
            process = subprocess.Popen(
                [sys.executable, "-B", "-X", "utf8", *command], cwd=ROOT,
                stdout=output, stderr=subprocess.STDOUT)
            while process.poll() is None:
                try:
                    process.wait(timeout=15)
                except subprocess.TimeoutExpired:
                    print(f"{name}: running; log {log.relative_to(ROOT)}", flush=True)
        report["stages"].append({
            "name": name, "status": "passed" if process.returncode == 0 else "failed",
            "returncode": process.returncode, "seconds": round(time.monotonic() - began, 3),
            "log": str(log.relative_to(ROOT)),
        })
        print(f"{name}: {report['stages'][-1]['status']}", flush=True)
        if process.returncode:
            break
    passed = len(report["stages"]) == len(commands) and all(
        stage["status"] == "passed" for stage in report["stages"])
    report["status"] = "static_baseline_ready" if passed else "failed"
    semantics_path = ROOT / "reports/player_semantics.json"
    if any(stage["name"] == "engine_semantics" for stage in report["stages"]) and semantics_path.exists():
        semantics = read_json(semantics_path)
        report["semantic_gate"] = semantics["semantic_gate"]
        report["semantic_summary"] = semantics["summary"]
        report["device_testing_allowed"] = passed and semantics["device_testing_allowed_by_semantic_gate"]
        if passed and semantics["semantic_gate"] != "passed":
            report["status"] = "regressions_passed_semantics_incomplete"
    if args.android and passed:
        report["android"] = "shared_vm_debug_player_apk_built"
    write_json(ROOT / "reports/player_pipeline.json", report)
    return 0 if passed and (
        not args.require_semantic_completion or report["semantic_gate"] == "passed") else 1


if __name__ == "__main__":
    raise SystemExit(main())
