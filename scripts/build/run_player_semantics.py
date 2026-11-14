"""Run engine behavior suites without launching games or touching a device."""
from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
import subprocess
import sys
import time

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "analysis"))
from audit_player_semantics import source_identity
from port_common import ROOT, archive_dir, read_json, timestamp, write_json


# Suite success is a regression result. The independent semantic inventory
# remains the acceptance gate and does not infer full coverage from this list.
# Profile recovery is a separate research command: regression runs must not
# regenerate runtime sources or replace production feature discovery.
SUITES = (
    "test_sprite_alpha_terminal",
    "test_numeric_semantics",
    "test_storage_semantics",
    "test_load_semantics",
    "test_control_semantics",
    "test_case_semantics",
    "test_return_semantics",
    "test_local_initialization",
    "test_string_semantics",
    "test_reference_storage",
    "test_argument_semantics",
    "test_optional_argument_slots",
    "test_call_semantics",
    "test_execution_remaining",
    "test_scalar_syscalls",
    "test_array_syscalls",
    "test_event_syscalls",
    "test_configuration_queries",
    "test_voice_settings",
    "test_voice_controls",
    "test_voice_playback",
    "test_audio_remaining",
    "test_screen_semantics",
    "test_graphics_remaining",
    "test_ultra_eligibility",
    "test_music_requests",
    "test_extsound_semantics",
    "test_media_pause",
    "test_paused_save",
    "test_native_saves",
    "test_player_native_saves",
    "test_story_save_layout",
    "test_player_story_saves",
    "test_android_audio_lifecycle",
    "dependency_restore_render_test",
    "audit_text_controls",
    "test_text_semantics",
    "test_text_remaining",
    "test_execution_lifecycle",
    "test_dialogs_semantics",
    "test_runtime_queries",
    "test_platform_control_semantics",
    "test_audio_completion",
    "test_game_icons",
    "test_raw_player_patch",
    "test_narcissu_patches",
    "test_online_translation",
    "test_online_voice_dialogue",
    "test_translated_string_comparison",
    "test_sprite_priority_groups",
    "test_fontout_zero_height",
    "test_first_frame_publication",
    "test_touch_callback_coordinates",
    "test_menu_focus_semantics",
    "test_touchscreen_back_semantics",
    "test_translation_service",
    "test_translation_live_controls",
    "test_new_graphics_abi",
    "test_original_reference",
    "test_read_skip_page_clear",
    "test_new_ui_abi",
    "test_new_archive_abi",
    "test_new_control_abi",
    "test_png_mask_resources",
    "test_page_image_loading",
    "test_save_identity_partitions",
    "test_save_upgrade",
    "test_startup_resources",
    "test_patch_archive_frames",
    "test_original_png_aliases",
    "test_patch_manifest",
    "test_image_extension_resolution",
    "test_media_session_owner",
    "test_android_exit_lifecycle",
    "test_configuration_quotes",
    "test_audio_preparation_cleanup",
    "test_pixel_reentry",
    "test_android_movie_compile",
    "test_engine_format_recognition",
    "test_engine_features",
    "test_text_mode",
    "test_engine_production_profiles",
    "test_player_snapshot_numbers",
    "test_player_file_numbers",
    "test_player_readmark_owners",
    "test_player_primary_thread",
    "test_player_hashing",
    "test_player_graphics_edges",
    "test_player_graphics_contract",
    "test_player_extcopy_overlap",
    "test_player_page_lifetimes",
    "test_player_sprite_interpolation",
    "test_player_tile_code_integrity",
    "test_emulator_host_restore",
    "test_emulator_audio_fades",
    "test_emulator_movie_lifecycle",
    "test_emulator_dialog_owners",
    "test_emulator_sdl_input",
    "test_emulator_document_media_io",
    "test_emulator_legacy_audio",
)
from run_emulator_host_audit import CASES as HOST_SUITES
SUITES = tuple(dict.fromkeys((*SUITES, *HOST_SUITES,
    "test_player_core", "test_player_images", "test_vm", "test_branch4",
    "test_array_indices", "test_script_filename_aliases", "test_event_dispatch_semantics",
    "test_cross_thread_events", "test_hover_transition_order", "test_archive_layers",
    "test_sprite_argument_forms", "test_tile_actions",
    "test_player_pixel_transactions", "test_player_capture_sequences",
    "test_player_history_clear", "test_player_screen_coordinates",
    "test_graphics_batch", "test_sprite_variable_geometry", "test_new_sprite_sequences",
    "test_native_pixels", "test_original_blend", "test_original_effects", "test_original_swap",
    "test_drawmodes", "test_remaining_pixel_calls", "test_graphics_checkpoint",
    "test_text_raster", "test_tile_routines", "test_tiles_extra", "test_capture_cache_batch",
    "test_text_ui_batch")))

# These suites read immutable source/EXE inputs and publish separate reports.
# Builds and host/model suites stay sequential.
PARALLEL_SUITES = frozenset({
    "test_numeric_semantics","test_storage_semantics","test_load_semantics",
    "test_control_semantics","test_case_semantics","test_return_semantics",
    "test_local_initialization","test_string_semantics","test_reference_storage",
    "test_argument_semantics","test_call_semantics","test_execution_remaining","test_scalar_syscalls",
    "test_array_syscalls","test_event_syscalls","test_configuration_queries",
    "test_voice_settings","test_voice_controls","test_voice_playback",
    "test_audio_remaining","test_screen_semantics","test_ultra_eligibility",
    "test_music_requests","test_extsound_semantics","test_text_semantics",
    "test_text_remaining",
})


def main():
    began_pipeline=time.monotonic()
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--inventory-only", action="store_true")
    parser.add_argument("--suite", action="append", choices=SUITES)
    parser.add_argument("--require-complete", action="store_true",
                        help="fail while complete engine semantics remain unverified")
    parser.add_argument("--jobs",type=int,default=3,choices=range(1,9),
                        help="concurrent independent comparison processes; builds remain sequential")
    args = parser.parse_args()
    folder = archive_dir("player_semantics")
    previous=ROOT/"reports/player_semantic_suites.json"
    costs={row["name"]:row["seconds"]for row in read_json(previous).get("stages",[])}if previous.exists()else{}
    before = source_identity()
    report = {
        "schema": 1, "timestamp": timestamp(), "stages": [],
        "source_identity": before, "evidence": str(folder.relative_to(ROOT)),
        "complete_engine_semantics": False, "device_tests_executed": False,
        "story_routes_executed": False,
        "comparison_jobs":args.jobs,
    }
    stages = [] if args.inventory_only else list(args.suite or SUITES)
    stages.append("audit_player_semantics")
    def execute(name):
        log = folder / f"{folder.name[:8]}_{name}.txt"
        command = [sys.executable, "-B", "-X", "utf8", *(
            ["scripts/analysis/audit_player_semantics.py"] if name=="audit_player_semantics" else
            ["scripts/build/run_emulator_audit.py","--case",name])]
        began = time.monotonic()
        with log.open("wb") as output:
            process = subprocess.Popen(command, cwd=ROOT, stdout=output, stderr=subprocess.STDOUT)
            while process.poll() is None:
                try:
                    process.wait(timeout=15)
                except subprocess.TimeoutExpired:
                    print(f"{name}: running; {log.relative_to(ROOT)}", flush=True)
        row = {"name": name, "status": "passed" if process.returncode == 0 else "failed",
               "returncode": process.returncode, "seconds": round(time.monotonic() - began, 3),
               "log": str(log.relative_to(ROOT))}
        return row

    def completed(row):
        report["stages"].append(row)
        write_json(ROOT / "reports/player_semantic_suites.json", report)
        print(f"{row['name']}: {row['status']} ({row['seconds']}s)", flush=True)

    cursor=0
    with ThreadPoolExecutor(max_workers=args.jobs)as pool:
        while cursor<len(stages):
            name=stages[cursor]
            if name not in PARALLEL_SUITES or args.jobs==1:
                completed(execute(name))
                cursor+=1
                continue
            batch=[]
            while cursor<len(stages)and stages[cursor]in PARALLEL_SUITES:
                batch.append(stages[cursor]);cursor+=1
            batch.sort(key=lambda name:costs.get(name,0),reverse=True)
            pending={pool.submit(execute,name):name for name in batch}
            for future in as_completed(pending):
                completed(future.result())
    order={name:index for index,name in enumerate(stages)}
    report["stages"].sort(key=lambda row:order[row["name"]])
    after = source_identity()
    report["source_changed_during_suites"] = before != after
    report["source_identity_after"] = after
    report["elapsed_seconds"]=round(time.monotonic()-began_pipeline,3)
    failed = [row["name"] for row in report["stages"] if row["status"] != "passed"]
    report["failed_suites"] = failed
    report["regression_status"] = ("inconclusive" if before != after else
                                   "failed" if failed else "passed")
    inventory = read_json(ROOT / "reports/player_semantics.json")
    report["semantic_gate"] = inventory["semantic_gate"]
    report["complete_engine_semantics"] = inventory["complete"]
    report["status"] = "regressions_passed_semantics_incomplete" if (
        report["regression_status"] == "passed") else report["regression_status"]
    write_json(folder / (folder.name[:8] + "_semantic_suites.json"), report)
    write_json(ROOT / "reports/player_semantic_suites.json", report)
    print(f"regressions: {report['regression_status']}; "
          f"full engine semantic gate: {report['semantic_gate']}", flush=True)
    if failed or before != after or (args.require_complete and inventory["semantic_gate"] != "passed"):
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
