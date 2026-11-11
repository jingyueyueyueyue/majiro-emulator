"""Independent host regression bundle; evidence stays in the dated workspace archive."""
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor, as_completed
import argparse
import json
import os
import importlib
import subprocess
import sys
import time

ROOT = Path(__file__).resolve().parents[2]
EVIDENCE = ROOT.parent/"archive/2026-11/20261111_emulator_audit_host/phase2"
CASES = [
    "test_emulator_host_restore", "test_emulator_audio_fades", "test_emulator_movie_lifecycle",
    "test_emulator_dialog_owners", "test_emulator_sdl_input", "test_external_saves",
    "test_emulator_document_media_io", "test_emulator_legacy_audio",
    "test_emulator_glyph_contract",
    "test_paused_save", "test_media_pause", "test_audio_completion", "test_audio_remaining",
    "test_audio_preparation_cleanup", "test_media_session_owner", "test_android_audio_lifecycle",
    "test_movie_completion", "test_native_slot_original", "test_native_slot_view",
    "test_native_slot_roundtrip", "test_native_slot_source", "test_window_menu_queue",
    "test_original_window_menu_dispatch", "test_touchscreen_back_semantics",
    "test_touch_callback_coordinates", "test_area_keyboard_defaults",
    "test_menu_focus_semantics", "test_hover_transition_order",
    "test_player_native_saves", "test_player_story_saves", "test_native_import_resume",
    "test_platform_control_semantics", "test_voice_settings", "test_voice_controls",
    "test_voice_playback", "test_dialogs_semantics", "test_dialogs_recovery",
    "test_android_dialogs", "test_android_exit_lifecycle", "test_host_bridge_audit",
    "test_android_movie_compile", "test_save_identity_partitions",
    "test_music_requests", "test_extsound_semantics",
    "test_host_load_atomicity",
    "test_host_movie_detach",
    "test_host_load_journal_work",
    "test_host_restore_combinations",
]


def case(name, arguments):
    sys.path[:0] = [str(ROOT/"scripts/analysis"), str(ROOT/"scripts/convert")]
    import port_common
    recorded={}
    original_write=port_common.write_json

    def archive(label):
        # Existing fixtures publish paths relative to their recovered project.
        target = ROOT/"archive/2026-11/20261111_emulator_audit_host/phase2"/name/(
            time.strftime("%Y%m%d_%H%M%S")+"_"+label)
        target.mkdir(parents=True, exist_ok=True)
        return target

    port_common.archive_dir = archive
    def write_report(path,value):
        if isinstance(value,dict)and "status"in value:
            recorded[str(path)]={key:value[key]for key in
                ("status","comparisons","mismatches","source_changed_during_test","source_changed")
                if key in value}
        return original_write(path,value)
    port_common.write_json=write_report
    sys.argv = [name+".py", *arguments]
    try:
        module=importlib.import_module(name)
        entry=getattr(module,"main",None)or getattr(module,"run")
        result=entry()
    except BaseException as error:
        EVIDENCE.mkdir(parents=True,exist_ok=True)
        original_write(EVIDENCE/("20261111_"+name+"_status.json"),{
            "test":name,"status":"failed","exception":repr(error),"reports":recorded,
            "failed_reports":{path:value for path,value in recorded.items()
                              if value.get("status")!="passed"}})
        raise
    rejected={"failed","incomplete","source_changed","inconclusive"}
    failures={path:value for path,value in recorded.items()if value.get("status")in rejected}
    if isinstance(result,dict)and result.get("status")in rejected:failures["return"]=result
    code=result if isinstance(result,int)else 0
    summary={"test":name,"status":"failed"if code or failures else"passed",
        "return_code":code,"reports":recorded,"failed_reports":failures}
    EVIDENCE.mkdir(parents=True,exist_ok=True)
    original_write(EVIDENCE/("20261111_"+name+"_status.json"),summary)
    return code or int(bool(failures))


def run_one(name):
    began = time.monotonic()
    result = subprocess.run([sys.executable, str(Path(__file__).resolve()), "--case", name],
                            cwd=ROOT, capture_output=True, timeout=900,
                            env={**os.environ, "PYTHONUTF8": "1"})
    log = EVIDENCE/("20261111_"+name+".log")
    log.write_bytes(result.stdout+result.stderr)
    return {"test": name, "status": "passed" if result.returncode == 0 else "failed",
            "exit_code": result.returncode, "seconds": round(time.monotonic()-began, 2),
            "log": str(log), "summary": result.stdout.decode("utf8", errors="replace")[-1200:]}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--case")
    parser.add_argument("--select", nargs="*")
    args, extra = parser.parse_known_args()
    if args.case:
        raise SystemExit(case(args.case, extra))
    EVIDENCE.mkdir(parents=True, exist_ok=True)
    rows = []
    selected = args.select or CASES
    def publish(report):
        text=json.dumps(report,ensure_ascii=True,indent=2)+"\n"
        (EVIDENCE/"20261111_test_results.json").write_text(text,encoding="utf8")
        target=ROOT/"reports/emulator_audit_host.json"
        if target.exists():
            audit=json.loads(target.read_text(encoding="utf8"))
            audit["status"]="phase2_"+report["status"]
            audit["source_frozen"]=False
            audit["phase2"]={"status":report["status"],"expected":report["expected"],
                "tests":[{k:v for k,v in row.items()if k!="summary"}for row in report["tests"]],
                "evidence":str(EVIDENCE),"device_accessed":False}
            target.write_text(json.dumps(audit,ensure_ascii=False,indent=2)+"\n",encoding="utf8")
    with ThreadPoolExecutor(max_workers=3) as pool:
        pending = {pool.submit(run_one, name): name for name in selected}
        for task in as_completed(pending):
            name = pending[task]
            try:
                row = task.result()
            except Exception as error:
                row = {"test": name, "status": "failed", "error": str(error)}
            rows.append(row)
            report = {"status": "running", "tests": sorted(rows, key=lambda row: row["test"]),
                      "expected": len(selected), "device_accessed": False}
            publish(report)
            print(name+": "+row["status"], flush=True)
    report["status"] = "passed" if all(row["status"] == "passed" for row in rows) else "failed"
    publish(report)
    if report["status"] != "passed":
        raise SystemExit(1)


if __name__ == "__main__":
    main()
