"""Single development build/check entrypoint. Incomplete gameplay is never a release."""
from __future__ import annotations
import argparse
import json
from pathlib import Path
import shutil
import subprocess
import sys
import time

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "convert"))
from port_common import ROOT, archive_dir, config, read_json, timestamp, write_json


def feedback_summary(report):
    if report["status"] != "development_test_ready":
        return
    build = read_json(ROOT / "reports/windows_build.json")["build_id"]
    native_menus = read_json(ROOT / "reports/native_menu_composition_tests.json")
    assert native_menus["build_id"] == build
    detail_path = ROOT / "reports/ui_detail_fixes.json"
    if detail_path.exists():
        detail = read_json(detail_path)
        detail.update(build_id=build, native_cases=native_menus["cases"],
                      pipeline_status=report["status"], pipeline_timestamp=report["timestamp"],
                      pipeline_checks_passed=sum(s["status"] == "passed" for s in report["stages"]))
        write_json(detail_path, detail)
    feedback_path = ROOT / "reports/feedback_fixes.json"
    if feedback_path.exists():
        feedback = read_json(feedback_path)
        feedback.update(build_id=build,
            native_dialogue_status=read_json(ROOT / "reports/native_dialogue_tests.json")["status"],
            native_save_restart_status=read_json(ROOT / "reports/native_save_tests.json")["status"])
        write_json(feedback_path, feedback)
    paths = {
        "all_resources": "all_assets.json", "fast_response": "fast_response_tests.json",
        "native_fast_response": "native_fast_response.json",
        "script_calls": "script_call_tests.json", "root_return": "root_return_tests.json",
        "persistent_globals": "persistent_global_tests.json", "music_controls": "music_menu_tests.json",
        "native_intro_loop": "native_audio_loop.json", "comma_fields": "strsplit_tests.json",
        "cg_buttons": "original_swap_tests.json", "viewed_cg": "picture_progress_tests.json",
        "read_scripts": "script_read_tests.json", "fullwidth_resource_names": "resource_key_tests.json",
        "earned_extras": "ending_extras_tests.json", "native_extras": "native_extras_tests.json"}
    checks = {name: {"status": read_json(ROOT / "reports" / path)["status"],
                     "report": "reports/" + path} for name, path in paths.items()
              if (ROOT / "reports" / path).is_file()}
    assert all(c["status"] == "passed" for c in checks.values())
    write_json(ROOT / "reports/original_feature_fixes.json", {
        "status": "passed", "build_id": build, "timestamp": report["timestamp"],
        "scope": "shipped game features only", "checks": checks, "game_asset_copies": 0,
        "full_gameplay_complete": False})


def finish_cleanup():
    """Retry only housekeeping after every game/build check already passed."""
    report = read_json(ROOT / "reports/port_pipeline.json")
    trailing = {"compact_evidence": "scripts/analysis/compact_evidence.py",
                "space": "scripts/analysis/storage_audit.py"}
    assert report["status"] == "failed"
    assert all(stage["status"] == "passed" for stage in report["stages"]
               if stage["name"] not in trailing), "game/build stages require a normal pipeline run"
    from build_windows import verify
    verify()  # Runtime, source and generated resources must still match the tested build.
    evidence = archive_dir("pipeline_cleanup")
    report["cleanup_retry"] = {"previous_timestamp": report["timestamp"], "evidence": str(evidence)}
    base = [sys.executable, "-B", "-X", "utf8"]
    for stage in report["stages"]:
        if stage["name"] not in trailing:
            continue
        began = time.monotonic()
        log = evidence / ("20261109_" + stage["name"] + ".txt")
        with log.open("wb") as output:
            result = subprocess.run([*base, trailing[stage["name"]]], cwd=ROOT,
                                    stdout=output, stderr=subprocess.STDOUT, timeout=55)
        stage.update(status="passed" if result.returncode == 0 else "failed",
                     returncode=result.returncode, seconds=round(time.monotonic()-began, 3),
                     log=str(log.relative_to(ROOT)))
        print(stage["name"] + ": " + stage["status"], flush=True)
        if result.returncode:
            break
    passed = all(stage["status"] == "passed" for stage in report["stages"])
    report.update(status="development_test_ready" if passed else "failed", timestamp=timestamp())
    write_json(ROOT / "reports/port_pipeline.json", report)
    write_json(evidence / "20261109_pipeline.json", report)
    feedback_summary(report)
    return 0 if passed else 1


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--verify-only", action="store_true",
                        help="verify existing inputs and build; never regenerate formal resources or binaries")
    parser.add_argument("--finish-cleanup", action="store_true",
                        help="retry housekeeping only after all game/build checks passed and build inputs still verify")
    args = parser.parse_args()
    if args.finish_cleanup:
        assert not args.verify_only
        return finish_cleanup()
    if shutil.disk_usage(ROOT).free < config()["minimum_free_bytes"]:
        raise RuntimeError("free space below configured minimum")
    evidence = archive_dir("port_pipeline")
    base = [sys.executable, "-B", "-X", "utf8"]
    checks = [
        ("references", "scripts/build/fetch_references.py", ["--verify-only"] if args.verify_only else []),
    ]
    if(ROOT/"localization/patch.lock.json").exists():
        checks.append(("translation","scripts/analysis/inspect_translation.py",[]))
    checks.append(("source_integrity"if args.verify_only else"prepare_sources","scripts/build/prepare_sources.py",
                   ["--verify-only"]if args.verify_only else []))
    if not args.verify_only:
        checks.append(("native_build", "scripts/build/build_native.py", []))
    checks.append(("all_assets", "scripts/build/prepare_all_assets.py",
                   ["--verify-only"] if args.verify_only else []))
    if(ROOT/"localization/patch.lock.json").exists():
        checks.append(("translation_runtime","scripts/analysis/test_translation.py",[]))
    if(ROOT/"localization/proofread_map.json").exists():
        checks.append(("proofread_roundtrip","scripts/analysis/test_proofread.py",[]))
    if(ROOT/"localization/choice_review.json").exists():
        checks.append(("choice_text","scripts/analysis/audit_choice_text.py",[]))
    if(ROOT/"localization/ui_images.json").exists():
        checks.append(("localized_ui","scripts/build/build_localized_ui.py",
                       ["--verify-only"]if args.verify_only else []))
        checks.append(("localized_ui_pixels","scripts/analysis/test_ui_images.py",[]))
    checks.extend([
        ("formats", "scripts/analysis/test_formats.py", []),
        ("vm", "scripts/analysis/test_vm.py", []),
        ("event_handlers", "scripts/analysis/test_event_handlers.py", []),
        ("opcode_execution", "scripts/analysis/test_opcode_execution.py", []),
        ("branch4", "scripts/analysis/test_branch4.py", []),
        ("missing_picture_flags", "scripts/analysis/test_missing_picture_flags.py", []),
        ("runtime_paths", "scripts/analysis/audit_runtime_paths.py", []),
        ("script_calls", "scripts/analysis/test_script_calls.py", []),
        ("root_return", "scripts/analysis/test_root_return.py", []),
        ("chapter_return", "scripts/analysis/test_chapter_return.py", ["--all"]),
        ("persistent_globals", "scripts/analysis/test_persistent_globals.py", []),
        ("audio_paths", "scripts/analysis/test_audio_paths.py", []),
        ("audio_absence", "scripts/analysis/test_audio_absence.py", []),
        ("audio_transport", "scripts/analysis/test_audio_transport.py", []),
        ("voice_semantics", "scripts/analysis/test_voice_semantics.py", []),
        ("music_menu", "scripts/analysis/test_music_menu.py", ["--verify-only"]if args.verify_only else []),
        ("array_indices", "scripts/analysis/test_array_indices.py", []),
        ("text_controls", "scripts/analysis/test_text_controls.py", []),
        ("string_matching", "scripts/analysis/test_strmatch.py", []),
        ("comma_fields", "scripts/analysis/test_strsplit.py", []),
        ("viewed_pictures", "scripts/analysis/test_picture_progress.py", []),
        ("script_read_records", "scripts/analysis/test_script_read.py", []),
        ("resource_keys", "scripts/analysis/test_resource_keys.py", []),
        ("playback_modes", "scripts/analysis/test_playback_modes.py", []),
        ("fast_response", "scripts/analysis/test_fast_response.py", []),
        ("fast_pacing", "scripts/analysis/test_fast_pacing.py", []),
        ("ui_guards", "scripts/analysis/test_ui_guards.py", []),
        ("screen_semantics", "scripts/analysis/test_screen_semantics.py", []),
        ("virtual_time", "scripts/analysis/test_virtual_time.py", []),
        ("expired_handles", "scripts/analysis/test_expired_handles.py", []),
        ("images", "scripts/analysis/test_images.py", []),
        ("pixels", "scripts/analysis/test_native_pixels.py", []),
        ("original_blend", "scripts/analysis/test_original_blend.py", []),
        ("original_swap", "scripts/analysis/test_original_swap.py", []),
        ("original_effects", "scripts/analysis/test_original_effects.py", []),
        ("remaining_pixel_calls", "scripts/analysis/test_remaining_pixel_calls.py", []),
        ("text_raster", "scripts/analysis/test_text_raster.py", []),
        ("draw_modes", "scripts/analysis/test_drawmodes.py", []),
        ("save_store", "scripts/analysis/test_save_store.py", []),
        ("save_graphics", "scripts/analysis/test_graphics_checkpoint.py", []),
        ("save_roundtrip", "scripts/analysis/test_saves.py", ["--verify-only"]if args.verify_only else []),
        ("presentation", "scripts/analysis/test_presentation.py", []),
        ("render_benchmark","scripts/analysis/benchmark_presenter.py",["--label","optimized","--operation","renderfast"]),
        ("presenter_pixels","scripts/analysis/test_presenter_speed.py",[]),
        ("source_exe", "scripts/analysis/inspect_native_calls.py", []),
        ("bootstrap", "scripts/analysis/probe_bootstrap.py", ["--verify-only"] if args.verify_only else []),
        ("opening_path","scripts/analysis/probe_title_paths.py",["--choice","1"]+(["--verify-only"]if args.verify_only else [])),
        ("settings_path","scripts/analysis/probe_title_paths.py",["--choice","3"]+(["--verify-only"]if args.verify_only else [])),
        ("settings_controls","scripts/analysis/test_settings.py",["--verify-only"]if args.verify_only else []),
        ("story_settings_return","scripts/analysis/probe_ui_paths.py",
            ["--scene","config"]+(["--verify-only"]if args.verify_only else [])),
        ("history_return","scripts/analysis/probe_ui_paths.py",
            ["--scene","history"]+(["--verify-only"]if args.verify_only else [])),
        ("display_fidelity","scripts/analysis/test_display_fidelity.py",["--verify-only"]if args.verify_only else []),
        ("dialogue_layout","scripts/analysis/test_dialogue_layout.py",["--verify-only"]if args.verify_only else []),
        ("speaker_metadata","scripts/analysis/test_speaker_metadata.py",[]),
        ("menu_composition","scripts/analysis/test_menu_composition.py",["--verify-only"]if args.verify_only else []),
        ("menu_transitions","scripts/analysis/test_menu_transitions.py",["--verify-only"]if args.verify_only else []),
        ("story_200_pages","scripts/analysis/run_story_campaign.py",["--pages","200"]+(["--verify-only"]if args.verify_only else [])),
        ("title_render","scripts/analysis/test_title_render.py",[]),
    ])
    earned=ROOT/"reports/ending_extras_tests.json"
    if earned.exists():
        checkpoint=read_json(earned)["checkpoint"]
        if (ROOT/checkpoint/"index.mjs").is_file():
            checks.append(("ending_extras","scripts/analysis/probe_ending_extras.py",
                ["--checkpoint",checkpoint]+(["--verify-only"]if args.verify_only else [])))
    if not args.verify_only:
        checks.append(("windows_build", "scripts/build/build_windows.py", []))
    checks.extend([
        ("windows_integrity", "scripts/build/build_windows.py", ["--verify-only"]),
        ("workspace_dependencies", "scripts/analysis/audit_workspace_dependencies.py", []),
        ("artemis_native", "scripts/analysis/verify_native.py", []),
        ("artemis_opening","scripts/analysis/verify_native.py",["--scenario","start"]),
        ("artemis_settings","scripts/analysis/verify_native.py",["--scenario","config"]),
        ("artemis_dialogue","scripts/analysis/verify_dialogue_native.py",[]),
        ("artemis_menu_composition","scripts/analysis/verify_menu_native.py",[]),
        ("artemis_confirmation","scripts/analysis/verify_confirmation_native.py",[]),
        ("artemis_fullscreen","scripts/analysis/verify_fullscreen.py",[]),
        ("artemis_save_restart","scripts/analysis/verify_saves_native.py",[]),
        ("artemis_chapter_return","scripts/analysis/verify_chapter_return_native.py",["--all"]),
        ("artemis_movie","scripts/analysis/verify_movie.py",[]),
        ("artemis_audio","scripts/analysis/verify_audio.py",[]),
        ("artemis_voice","scripts/analysis/verify_voice_native.py",[]),
        ("artemis_fast","scripts/analysis/verify_fast_native.py",[]),
        ("artemis_audio_loop","scripts/analysis/verify_audio_loop.py",[]),
        ("title_performance","scripts/analysis/verify_performance.py",["--scene","title","--duration","6000"]),
        ("startup_performance","scripts/analysis/verify_performance.py",["--scene","intro","--duration","7000"]),
        ("opening_performance","scripts/analysis/verify_performance.py",["--scene","start","--duration","23000"]),
        ("coast_transition","scripts/analysis/verify_performance.py",["--scene","coast","--duration","10500"]),
        ("settings_transition_native","scripts/analysis/verify_performance.py",["--scene","menu_config","--duration","3000"]),
        ("pause_menu_performance","scripts/analysis/verify_performance.py",["--scene","menu_pause","--duration","3000"]),
        ("save_transition_native","scripts/analysis/verify_performance.py",["--scene","menu_save","--duration","3000"]),
        ("load_transition_native","scripts/analysis/verify_performance.py",["--scene","menu_load","--duration","3000"]),
        ("compact_evidence", "scripts/analysis/compact_evidence.py", []),
        ("space", "scripts/analysis/storage_audit.py", []),
    ])
    if earned.exists():
        checks.insert(next(i for i,row in enumerate(checks)if row[0]=="compact_evidence"),
            ("artemis_extras","scripts/analysis/verify_extras_native.py",[]))
    stages = []
    failed = False
    for label, script, options in checks:
        if failed:
            stages.append({"name": label, "status": "skipped"})
            continue
        began = time.monotonic()
        log = evidence / (evidence.name[:8]+"_" + label + ".txt")
        try:
            with log.open("wb") as stream:
                result = subprocess.run([*base, script, *options], cwd=ROOT,
                                        stdout=stream, stderr=subprocess.STDOUT,
                                        timeout=1800 if label=="all_assets"else
                                        240 if label in("chapter_return","artemis_chapter_return")else
                                        200 if label=="artemis_audio_loop"else
                                        170 if label in("artemis_movie","artemis_menu_composition","artemis_extras")else
                                        90 if label=="ending_extras"else
                                        120 if label=="artemis_dialogue"else
                                        90 if label=="dialogue_layout"else 55)
            status = "passed" if result.returncode == 0 else "failed"
            code = result.returncode
        except subprocess.TimeoutExpired:
            status, code = "timed_out", None
        stages.append({"name": label, "status": status, "returncode": code,
                       "seconds": round(time.monotonic() - began, 3),
                       "log": str(log.relative_to(ROOT))})
        print(label + ": " + status, flush=True)
        if status != "passed":
            failed = True
    bootstrap = read_json(ROOT / "reports/bootstrap_probe.json") if (ROOT / "reports/bootstrap_probe.json").exists() else {}
    report = {"schema": 1, "timestamp": timestamp(),
              "status": "failed" if failed else "development_test_ready",
              "verify_only": args.verify_only, "stages": stages,
              "gameplay_status": "incomplete", "complete": False, "release_allowed": False,
              "current_blocker": bootstrap.get("problem"),
              "test_executable": "projects/windows/Ruri_Artemis.exe",
              "android_status": "not_implemented"}
    write_json(ROOT / "reports/port_pipeline.json", report)
    write_json(evidence / (evidence.name[:8]+"_pipeline.json"), report)
    feedback_summary(report)
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
