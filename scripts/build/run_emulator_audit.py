"""Build stable emulator sources once, then run original-code and host regressions."""
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
import argparse
import importlib
import subprocess
import sys
import time

ROOT = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(ROOT / "scripts/analysis"), str(ROOT / "scripts/convert")]
from port_common import archive_dir, read_json, sha256, timestamp, write_json
from audit_player_semantics import source_identity
from run_player_semantics import SUITES as SEMANTIC_SUITES
from run_emulator_host_audit import CASES as HOST_SUITES

EXTRA_SUITES = (
    "test_formats", "test_player_core", "test_player_images", "test_vm",
    "test_branch4", "test_array_indices", "test_script_filename_aliases",
    "test_event_dispatch_semantics", "test_cross_thread_events",
    "test_hover_transition_order", "test_native_slot_source", "test_archive_layers",
    "test_player_snapshot_numbers", "test_player_file_numbers", "test_player_readmark_owners",
    "test_player_graphics_edges", "test_player_graphics_contract", "test_player_sprite_interpolation",
    "test_player_tile_code_integrity", "test_sprite_argument_forms", "test_tile_actions",
    "test_chapter_menu_dispatch",
)
TEXT_RESOURCE_SUITES = {
    "test_text_mode", "test_patch_manifest", "test_raw_player_patch",
    "test_narcissu_patches", "test_archive_layers",
}
ORIGINAL_SUITES = tuple(dict.fromkeys((*SEMANTIC_SUITES, *HOST_SUITES, *EXTRA_SUITES)))
SUITES = tuple(dict.fromkeys("test_text_resources" if name in TEXT_RESOURCE_SUITES else name
                            for name in ORIGINAL_SUITES))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--build", action="store_true", help="build the PC core/pixels and Android ARM64 libraries once")
    parser.add_argument("--package", action="store_true", help="package the recovery APK after all selected checks pass")
    parser.add_argument("--suite", action="append", choices=tuple(dict.fromkeys((*SUITES,*ORIGINAL_SUITES))))
    parser.add_argument("--jobs", type=int, default=3, choices=range(1, 7))
    parser.add_argument("--case", choices=tuple(dict.fromkeys((*SUITES,*ORIGINAL_SUITES))), help=argparse.SUPPRESS)
    args = parser.parse_args()
    if args.case:
        name = args.case
        sys.argv = [str(ROOT / "scripts/analysis" / (name + ".py"))]
        module = importlib.import_module(name)
        entry = getattr(module, "main", None) or getattr(module, "run")
        result = entry()
        if isinstance(result, dict):
            return int(result.get("status") in ("failed", "incomplete", "source_changed", "inconclusive"))
        return result if isinstance(result, int) else 0
    folder = archive_dir("emulator_audit")
    identity = source_identity()
    selected = list(dict.fromkeys("test_text_resources" if name in TEXT_RESOURCE_SUITES else name
                                 for name in (args.suite or SUITES)))
    destination = ROOT / "reports/emulator_audit_validation.json"
    previous = read_json(destination) if destination.is_file() else {}
    costs = {row["name"]: row.get("seconds", 0) for row in previous.get("tests", [])}
    test_inputs = {str(path.relative_to(ROOT)): sha256(path)
                   for directory in ("analysis", "build", "convert")
                   for path in sorted((ROOT / "scripts" / directory).rglob("*"))
                   if path.is_file() and path.suffix in (".py", ".java", ".cpp", ".hpp", ".h", ".inc")}
    execution_order = sorted(selected, key=lambda name: costs.get(name, 0), reverse=True)
    report = {"timestamp": timestamp(), "status": "running", "source_identity": identity,
              "builds": [], "tests": [], "requested_suites": selected,
              "execution_order": execution_order, "test_source_sha256": test_inputs,
              "text_suite_aliases": sorted(TEXT_RESOURCE_SUITES),
              "device_accessed": False, "evidence": str(folder.relative_to(ROOT))}

    def publish():
        write_json(folder / "report.json", report)
        write_json(destination, report)

    def execute(name, relative, extra=()):
        began = time.monotonic()
        log = folder / (folder.name[:8] + "_" + name + ".txt")
        try:
            with log.open("wb") as stream:
                script_arguments = (["scripts/build/run_emulator_audit.py", "--case", name]
                                    if relative.startswith("scripts/analysis/") else [relative, *extra])
                result = subprocess.run([sys.executable, "-B", "-X", "utf8",
                                         *script_arguments],
                                        cwd=ROOT, stdout=stream, stderr=subprocess.STDOUT, timeout=1800)
            code = result.returncode
        except subprocess.TimeoutExpired:
            code = -1
        return {"name": name, "status": "passed" if code == 0 else "failed",
                "returncode": code, "seconds": round(time.monotonic() - began, 2),
                "log": str(log.relative_to(ROOT))}

    publish()
    if args.build:
        for name, extra in (("build_player_core", ("--android",)),
                            ("build_player_native", ()), ("build_android_game", ())):
            row = execute(name, "scripts/build/" + name + ".py", extra)
            report["builds"].append(row)
            publish()
            print(name + ": " + row["status"], flush=True)
            if row["returncode"]:
                report["status"] = "failed"
                publish()
                return 1
            if name == "build_player_native":
                # This build publishes the content-addressed DLL filename
                # into its generated Lua manifest, not a runtime code edit.
                after = source_identity()
                changed = {key for key in set(after["files"]) | set(identity["files"])
                           if after["files"].get(key) != identity["files"].get(key)}
                if changed - {"player/runtime/player_native_library.lua"}:
                    raise RuntimeError("emulator sources changed during pixel build")
                identity = after
                report["source_identity"] = identity
        if source_identity() != identity:
            raise RuntimeError("emulator sources changed during centralized build")
    with ThreadPoolExecutor(max_workers=args.jobs) as pool:
        pending = {pool.submit(execute, name, "scripts/analysis/" + name + ".py"): name
                   for name in execution_order}
        for future in as_completed(pending):
            row = future.result()
            report["tests"].append(row)
            publish()
            print(row["name"] + ": " + row["status"] + " (" + str(row["seconds"]) + "s)", flush=True)
    order = {name: index for index, name in enumerate(selected)}
    report["tests"].sort(key=lambda row: order[row["name"]])
    report["source_changed_during_validation"] = source_identity() != identity
    report["test_sources_changed"] = [name for name, digest in test_inputs.items()
                                     if not (ROOT / name).is_file() or sha256(ROOT / name) != digest]
    report["failed_suites"] = [row["name"] for row in report["tests"] if row["returncode"]]
    report["status"] = ("source_changed" if report["source_changed_during_validation"] or report["test_sources_changed"] else
                        "failed" if report["failed_suites"] else "passed")
    if args.package and report["status"] == "passed":
        row = execute("package_android", "scripts/build/build_android_probe.py", ("--recovery",))
        report["builds"].append(row)
        if row["returncode"]:
            report["status"] = "failed"
        else:
            report["apk"] = read_json(ROOT / "reports/android_probe_build.json")
    report["completed_at"] = timestamp()
    publish()
    return int(report["status"] != "passed")


if __name__ == "__main__":
    raise SystemExit(main())
