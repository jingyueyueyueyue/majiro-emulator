"""Run independent recovered-runtime regressions concurrently with per-suite logs."""
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor,as_completed
import argparse
import json
import subprocess
import sys
import time
sys.path[:0]=[str(Path(__file__).resolve().parents[1]/"convert")]
from port_common import ROOT,archive_dir,read_json,write_json,timestamp
from run_background_compatibility import source_identity

SUITES=(
    "test_sprite_alpha_terminal",
    "test_read_skip_page_clear",
    "test_original_reference","test_android_exit_lifecycle","test_new_ui_abi",
    "test_formats","test_encrypted_images","test_player_core","test_player_images",
    "test_vm","test_script_filename_aliases","test_voice_rect_fields",
    "test_menu_focus_semantics","test_hover_transition_order","test_touchscreen_back_semantics",
    "test_area_keyboard_defaults","test_event_dispatch_semantics",
    "test_original_window_menu_dispatch","test_window_menu_queue",
    "test_native_slot_original","test_native_slot_roundtrip","test_native_slot_view",
    "test_translated_string_comparison","test_sprite_priority_groups",
    "test_pixel_reentry","test_save_identity_partitions","test_paused_save",
    "test_media_session_owner","test_audio_preparation_cleanup","test_android_audio_lifecycle",
    "test_android_movie_compile","test_text_mode","test_translation_service",
    "test_translation_live_controls","test_online_voice_dialogue",
    "test_touch_callback_coordinates","test_page_image_loading","test_png_mask_resources",
    "test_patch_archive_frames","test_patch_manifest","test_engine_format_recognition",
    "test_configuration_quotes","test_optional_argument_slots","test_expired_handles",
    "test_native_pixels","test_text_raster","test_ui_images","test_proofread",
    "test_graphics_checkpoint","test_drawmodes","test_original_blend","test_original_swap",
    "test_original_effects","test_native_saves","test_player_native_saves",
    "test_player_story_saves","test_story_save_layout","test_media_pause",
    "test_music_menu","test_saves","test_settings_sliders","test_array_indices",
    "test_scalar_syscalls","test_array_syscalls","test_event_syscalls","test_script_calls",
    "test_return_semantics","test_argument_semantics","test_call_semantics",
    "test_execution_lifecycle","test_cross_thread_events","test_control_semantics",
    "test_branch4","test_case_semantics","test_numeric_semantics",
)

DETAIL_REPORTS={
    "test_scalar_syscalls":"scalar_syscall_tests.json",
    "test_array_syscalls":"array_syscall_tests.json",
    "test_event_syscalls":"event_syscall_tests.json",
    "test_return_semantics":"return_semantics_tests.json",
    "test_argument_semantics":"argument_semantics_tests.json",
    "test_call_semantics":"call_semantics_tests.json",
    "test_control_semantics":"control_semantics_tests.json",
    "test_case_semantics":"case_semantics_tests.json",
    "test_numeric_semantics":"numeric_semantics_tests.json",
}


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--suite",action="append",choices=SUITES)
    parser.add_argument("--jobs",type=int,default=6)
    args=parser.parse_args()
    folder=archive_dir("recovery_regression_batch")
    identity=source_identity()
    report={"timestamp":timestamp(),"status":"running","stages":[],"device_accessed":False,
            "source_identity":identity,"requested_suites":args.suite or list(SUITES),
            "evidence":str(folder.relative_to(ROOT))}
    def run(name):
        began=time.monotonic();began_ns=time.time_ns();log=folder/(name+".txt")
        try:
            result=subprocess.run([sys.executable,"-B","-X","utf8","scripts/analysis/"+name+".py"],
                                  cwd=ROOT,capture_output=True,timeout=300)
            log.write_bytes(result.stdout+result.stderr)
            row={"name":name,"returncode":result.returncode,"status":"passed"if not result.returncode else"failed",
                 "source_identity":identity,"seconds":round(time.monotonic()-began,2),
                 "log":str(log.relative_to(ROOT))}
            detail=ROOT/"reports"/DETAIL_REPORTS.get(name,"missing-suite-report")
            if result.returncode and detail.is_file() and detail.stat().st_mtime_ns>=began_ns:
                facts=read_json(detail)
                if facts.get("status")=="incomplete" and facts.get("mismatches")==0:
                    row.update(status="incomplete",detail_report=str(detail.relative_to(ROOT)),
                               comparisons=facts.get("comparisons"),
                               unsupported_sample_count=facts.get("unsupported_sample_count"),
                               unsupported_fixture_cases=len(facts.get("unsupported_fixtures",[])),
                               incomplete_scope_is_not_passed=True)
            return row
        except subprocess.TimeoutExpired as error:
            log.write_bytes((error.stdout or b"")+(error.stderr or b""))
            return {"name":name,"status":"timeout","seconds":round(time.monotonic()-began,2),
                    "log":str(log.relative_to(ROOT))}
    with ThreadPoolExecutor(max_workers=args.jobs)as pool:
        for future in as_completed([pool.submit(run,n)for n in(args.suite or SUITES)]):
            row=future.result();report["stages"].append(row)
            write_json(folder/"report.json",report);write_json(ROOT/"reports/recovery_regressions.json",report)
            print(row["name"],row["status"],row["seconds"],flush=True)
    report["status"]=("failed"if any(r["status"]in("failed","timeout")for r in report["stages"])
                      else"incomplete"if any(r["status"]=="incomplete"for r in report["stages"])
                      else"passed")
    if source_identity()!=identity:
        report.update(status="source_changed",source_changed_during_run=True)
    report["timestamp"]=timestamp()
    write_json(folder/"report.json",report);write_json(ROOT/"reports/recovery_regressions.json",report)
    aggregate_path=ROOT/"reports/recovery_regressions_aggregate.json"
    previous=read_json(aggregate_path)if aggregate_path.exists()else{}
    by_name={r["name"]:r for r in previous.get("stages",[])
             if previous.get("source_identity")==identity}
    by_name.update({r["name"]:r for r in report["stages"]})
    missing=[name for name in SUITES if name not in by_name]
    counts={state:sum(r["status"]==state for r in by_name.values())
            for state in("passed","failed","timeout","incomplete")}
    aggregate={"timestamp":timestamp(),"source_identity":identity,"stages":list(by_name.values()),
               "missing_suites":missing,"counts":counts,
               "status":("failed"if counts["failed"]or counts["timeout"]else
                         "incomplete"if missing or counts["incomplete"]else"passed"),
               "incomplete_scope_is_not_passed":True,"latest_run":report["evidence"]}
    write_json(aggregate_path,aggregate)
    return int(report["status"]!="passed")


if __name__=="__main__":raise SystemExit(main())
