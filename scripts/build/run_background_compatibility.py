"""Run the desktop compatibility queue with a process lock and durable evidence.

This runner detects and records failures. Code repairs are performed by the
thread's scheduled agent, which reviews these reports before changing sources.
"""
from pathlib import Path
import argparse
import hashlib
import json
import msvcrt
import os
import shutil
import subprocess
import sys
import time

sys.path[:0] = [str(Path(__file__).resolve().parents[1] / "convert")]
from port_common import ROOT, archive_dir, read_json, timestamp, write_json

QUEUE = (
    ("sprite_alpha_terminal", "scripts/analysis/test_sprite_alpha_terminal.py", [], 180),
    ("read_skip_page_clear", "scripts/analysis/test_read_skip_page_clear.py", [], 180),
    ("source_playback_controls", "scripts/analysis/test_source_playback_controls.py",
     ["--game", "Amber Quartz", "--game", "reconquista", "--game", "oh_pants",
      "--game", "magical_kanon2", "--game", "RURINOKASANE", "--game", "SWITCH",
      "--game", "White", "--game", "kiraoto", "--pages", "300", "--seconds", "240",
      "--jobs", "4"], 2700),
    ("first_frame", "scripts/analysis/test_first_frame_publication.py", [], 180),
    ("script_filename_aliases", "scripts/analysis/test_script_filename_aliases.py", [], 180),
    ("history_voice_fields", "scripts/analysis/test_voice_rect_fields.py", [], 180),
    ("startup_input_transactions", "scripts/analysis/test_startup_input_transactions.py",
     ["--negative-control", "--jobs", "4"], 3600),
    ("zero_height_font", "scripts/analysis/test_fontout_zero_height.py", [], 180),
    ("focus_and_back", "scripts/analysis/test_menu_focus_semantics.py", [], 180),
    ("hover_transition_order", "scripts/analysis/test_hover_transition_order.py", [], 180),
    ("area_keyboard_defaults", "scripts/analysis/test_area_keyboard_defaults.py", [], 180),
    ("event_dispatch_semantics", "scripts/analysis/test_event_dispatch_semantics.py", [], 180),
    ("original_window_menu_dispatch", "scripts/analysis/test_original_window_menu_dispatch.py", [], 180),
    ("window_menu_queue", "scripts/analysis/test_window_menu_queue.py", [], 180),
    ("native_slot_original", "scripts/analysis/test_native_slot_original.py", [], 180),
    ("native_slot_roundtrip", "scripts/analysis/test_native_slot_roundtrip.py", [], 180),
    ("native_slot_view", "scripts/analysis/test_native_slot_view.py", [], 180),
    ("touchscreen_back", "scripts/analysis/test_touchscreen_back_semantics.py", [], 180),
    ("history_layout_voice", "scripts/analysis/test_history_voice_batch.py", ["--pages", "300"], 1800),
    ("window_menu_source_paths", "scripts/analysis/test_window_menu_paths.py", ["--jobs", "4"], 1800),
    ("native_slot_source", "scripts/analysis/test_native_slot_source.py", ["--pages", "300"], 1200),
    ("save_thumbnail_pixels", "scripts/analysis/test_save_preview_batch.py", [], 1200),
    ("save_menu_pixels", "scripts/analysis/test_save_menu_preview.py", ["--pages", "300"], 1800),
    ("long_story_and_menus", "scripts/analysis/test_desktop_game_batch.py",
     ["--pages", "300", "--seconds", "180", "--jobs", "4"], 5400),
    ("startup_timing", "scripts/analysis/test_startup_timing_batch.py",
     ["--repeats", "8", "--jobs", "4"], 2400),
    ("chapter_entries", "scripts/analysis/test_chapter_regressions.py",
     ["--pages", "6", "--seconds", "3", "--jobs", "4"], 7200),
    ("runtime_regressions", "scripts/build/run_recovery_regressions.py", ["--jobs", "4"], 1800),
    ("port_assembly", "scripts/build/build_windows.py", [], 300),
    ("port_assets", "scripts/build/build_windows.py", ["--verify-only"], 1800),
)


def source_identity():
    digest = hashlib.sha256()
    for directory, suffixes in (("player", {".lua", ".java", ".cpp", ".hpp", ".h", ".inc", ".xml"}),
                                ("runtime", {".lua", ".cpp", ".hpp", ".h", ".inc"}),
                                ("scripts", {".py"}), ("config", {".json"})):
        for path in sorted((ROOT / directory).rglob("*")):
            if path.is_file() and path.suffix in suffixes:
                digest.update(path.relative_to(ROOT).as_posix().encode("utf8"))
                digest.update(hashlib.sha256(path.read_bytes()).digest())
    return digest.hexdigest()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--stage", action="append", choices=[q[0] for q in QUEUE])
    parser.add_argument("--resume", action="store_true",
                        help="Reuse completed stages only if their exact source identity still matches.")
    args = parser.parse_args()
    state_path = ROOT / "reports/background_compatibility_state.json"
    lock_path = ROOT / "reports/background_compatibility.lock"
    with lock_path.open("a+b") as lock:
        # Windows byte locks also reject reads. Check length without reading
        # the protected byte before attempting the nonblocking acquisition.
        lock.seek(0, os.SEEK_END)
        if lock.tell() == 0:
            lock.write(b"0")
            lock.flush()
        lock.seek(0)
        child = None
        try:
            msvcrt.locking(lock.fileno(), msvcrt.LK_NBLCK, 1)
        except OSError:
            print("An existing desktop compatibility queue already owns the lock.", flush=True)
            return 0
        identity = source_identity()
        previous = read_json(state_path) if args.resume and state_path.exists() else {}
        reuse = {r["name"]: r for r in previous.get("stages", [])
                 if r.get("source_identity") == identity and r.get("status") == "passed"}
        folder = archive_dir("background_compatibility")
        report = {"status": "running", "started": timestamp(), "updated": timestamp(),
                  "pid": os.getpid(), "source_identity": identity, "stages": [],
                  "evidence": str(folder.relative_to(ROOT)), "device_accessed": False,
                  "code_repair_owner": "scheduled agent in the current thread",
                  "full_compatibility_verified": False}

        def publish():
            report["updated"] = timestamp()
            write_json(folder / "report.json", report)
            write_json(state_path, report)

        try:
            publish()
            for name, script, extra, timeout in QUEUE:
                if args.stage and name not in args.stage:
                    continue
                report["free_disk_bytes"] = shutil.disk_usage(ROOT).free
                if report["free_disk_bytes"] < 6 * 1024**3:
                    report.update(status="resource_blocked",
                                  reason="Less than 6 GiB available for the next batch; review archived scratch first.")
                    break
                if name in reuse:
                    report["stages"].append(reuse[name] | {"reused": True})
                    publish()
                    continue
                if source_identity() != identity:
                    report["status"] = "source_changed"
                    report["reason"] = "Sources changed; subsequent tests need a fresh source snapshot."
                    break
                row = {"name": name, "status": "running", "started": timestamp(),
                       "source_identity": identity, "command": [script, *extra],
                       "log": str((folder / (name + ".txt")).relative_to(ROOT))}
                report["stages"].append(row)
                report["current_stage"] = name
                publish()
                began = time.monotonic()
                with (ROOT / row["log"]).open("wb") as output:
                    child = subprocess.Popen([sys.executable, "-B", "-X", "utf8", script, *extra],
                                             cwd=ROOT, stdout=output, stderr=subprocess.STDOUT,
                                             env=dict(os.environ, MAJIRO_COMPATIBILITY_QUEUE_PID=str(os.getpid())))
                    row["pid"] = child.pid
                    publish()
                    while child.poll() is None:
                        if time.monotonic() - began > timeout:
                            # End this owned process tree, including its worker processes.
                            subprocess.run(["taskkill", "/PID", str(child.pid), "/T", "/F"],
                                           capture_output=True, check=False)
                            child.wait(timeout=30)
                            row["status"] = "timeout"
                            break
                        row["seconds"] = round(time.monotonic() - began, 2)
                        publish()
                        time.sleep(5)
                    if row["status"] == "running":
                        row.update(returncode=child.returncode,
                                   status="passed" if child.returncode == 0 else "needs_review")
                if source_identity() != identity:
                    row["source_changed_during_run"] = True
                    row["status"] = "source_changed"
                row.update(finished=timestamp(), seconds=round(time.monotonic() - began, 2))
                publish()
                print(name, row["status"], row["seconds"], flush=True)
            else:
                report["status"] = ("needs_review" if any(r["status"] != "passed"
                                    for r in report["stages"]) else "desktop_queue_complete")
            report.pop("current_stage", None)
            report["finished"] = timestamp()
            publish()
            return int(report["status"] != "desktop_queue_complete")
        except BaseException as error:
            if child is not None and child.poll() is None:
                subprocess.run(["taskkill", "/PID", str(child.pid), "/T", "/F"],
                               capture_output=True, check=False)
            report.update(status="interrupted", error=str(error), finished=timestamp())
            publish()
            raise
        finally:
            lock.seek(0)
            msvcrt.locking(lock.fileno(), msvcrt.LK_UNLCK, 1)


if __name__ == "__main__":
    raise SystemExit(main())
