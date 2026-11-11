"""Publish the host audit scope, concrete fixes and centralized validation boundary."""
from pathlib import Path
import json
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]/"convert"))
from port_common import ROOT, sha256, timestamp, write_json

RUNTIME = (
    "majiro_save.lua", "majiro_native_import.lua", "majiro_media.lua", "majiro_input.lua",
    "majiro_window_menus.lua", "majiro_settings.lua", "majiro_slot_picker.lua",
    "majiro_dialogs.lua", "majiro_platform.lua", "majiro_external.lua", "player_boot.lua",
    "player_configuration.lua", "player_native_library.lua",
)
FIXES = [
    ("save-audio-generation", "majiro_save.lua",
     "Remap restored active and suspended event audio waits to the replacement transport; preserve stale waits."),
    ("load-transaction", "majiro_save.lua",
     "Local and external loads stage VM/private script copies and graphics before mutation; reuse validated scripts at commit."),
    ("persistent-settings", "majiro_save.lua;majiro_media.lua",
     "Preserve current master, voice, speed and font settings; recompute replay gain and reapply saved playback frequency."),
    ("audio-fade-owner", "GameBridge.java",
     "Channel fade tickets survive intro-loop promotion and are revoked by newer gain, replacement and stop; zero fade stops synchronously."),
    ("movie-preparation", "MovieTextures.java",
     "Resume waits for prepare and seek; queued lifecycle work uses latest suspension state; callback failures become media errors."),
    ("movie-wrapper", "majiro_media.lua",
     "Forward ordinary clear/rectangle arguments; play variants return after startup and movie_wait alone blocks; retire obsolete movie/texture state."),
    ("dialog-owner", "majiro_dialogs.lua;majiro_external.lua;ModalDialogState.java",
     "Reject changed, terminated or obsolete owner before polling/applying setup or authentication result; one terminal reply."),
    ("input-suspension", "game_host.cpp",
     "Clear keys/buttons on background/focus loss and suppress physical events while suspended."),
    ("saf-request", "ExternalSaveIO.java",
     "Claim each document result once and cancel an interrupted exchange so later requests can proceed."),
    ("ijk-cleanup", "IjkVideo.java",
     "Close descriptor and decoder on nonseekable source/bind failure; validate read ranges and zero-byte EOF reads."),
    ("glyph-contract", "GameBridge.java",
     "Load private face before measurement and rasterization; validate advances and bound the supersampled allocation."),
    ("production-profile-routing", "player_boot.lua;player_configuration.lua;majiro_input.lua;majiro_dialogs.lua",
     "Pass event_controls/string_search_case_sensitive; honor cursor-reset sentinel and warning-retry flags from observed engine profile."),
    ("oracle-coverage", "scripts/analysis/original_audio*.py and test_*",
     "Add real legacy audio oracle; recover stream field offsets relative to actual handle/pause; handle absent APIs/mode fields explicitly."),
]


def main():
    evidence = ROOT.parent/"archive/2026-11/20261111_emulator_audit_host"
    paths = sorted((ROOT/"player/android").rglob("*"))
    paths += [ROOT/"player/runtime"/name for name in RUNTIME]
    paths += [path for stem in ("majiro_save", "majiro_story_save", "majiro_native_import")
              for path in (ROOT/"player/core").glob(stem+".*")]
    files = []
    for path in paths:
        if not path.is_file():
            continue
        name = path.relative_to(ROOT).as_posix()
        if path.name == "EngineFeatures.java":
            coverage = "Main chat owns code-feature implementation; reviewed integration and profile pass-through only."
        elif path.suffix in (".png", ".xml"):
            coverage = "Resource/manifest integration inspected; no resource modification."
        elif path.name in ("CoreInstrumentation.java", "RenderInstrumentation.java"):
            coverage = "Instrumentation contract inspected; not run on a device."
        elif name.startswith("player/core"):
            coverage = "Format bounds/alias/layout/source-validation review; existing executable parser/import fixtures rerun before compile freeze."
        elif path.name in ("GameIcon.java", "TranslationSettingsDialog.java", "MainActivity.java", "SettingsActivity.java"):
            coverage = "Static review of parsing/callback/lifecycle/decompiled method boundaries; physical UI not executed."
        else:
            coverage = "Implementation and ownership/parameter/cleanup contracts reviewed; execution scope listed separately."
        files.append({"path": name, "sha256": sha256(path), "review": coverage})
    tests = []
    for name in ("voice_control_tests", "voice_playback_tests", "voice_settings_tests",
                 "dialog_semantics_tests", "dialog_recovery_tests", "audio_completion_tests",
                 "audio_remaining_tests", "platform_control_semantics_tests",
                 "native_slot_roundtrip", "native_slot_original", "original_window_menu_matrix",
                 "window_menu_queue", "area_keyboard_defaults", "external_save_tests",
                 "paused_save_tests", "media_pause_tests", "movie_completion_tests"):
        file = ROOT/"reports"/(name+".json")
        if file.exists():
            data = json.loads(file.read_text(encoding="utf8"))
            tests.append({"report": str(file), **{key: data[key] for key in (
                "status", "comparisons", "mismatches", "source_changed_during_test") if key in data},
                "scope": data.get("scope", "")})
    local = []
    for file in evidence.rglob("*.json"):
        if file.name in ("20261111_test_results.json", "emulator_audit_host.json"):
            continue
        try:
            data = json.loads(file.read_text(encoding="utf8"))
        except (ValueError, UnicodeError):
            continue
        if data.get("status") == "passed":
            local.append({"report": str(file), "scope": data.get("scope", ""),
                          "checks": len(data["checks"]) if isinstance(data.get("checks"), list) else data.get("checks")})
    report = {
        "schema": 2, "timestamp": timestamp(), "status": "source_frozen_awaiting_central_validation",
        "source_frozen": True, "scope": "Independent player only; root runtime never certifies this emulator",
        "baseline": "9245a47", "files": files, "fixes": [
            {"id": key, "files": path, "change": change} for key, path, change in FIXES],
        "tests": tests, "executed_local_evidence": local,
        "original_evidence": [
            "reports/dialog_recovery_tests.json: 48,384 original wrapper comparisons, 0 mismatches",
            "reports/voice_control_tests.json: 70,686 original control comparisons, 0 mismatches",
            "reports/voice_playback_tests.json: 4,812 original playback comparisons, 0 mismatches",
            str(evidence/"20261111_legacy_audio.json")+": 80 legacy control and 12 voice cases",
            str(evidence/"20261111_movie_play.txt")+": ordinary original wrapper at 0x44a800, decoder call 0x47e530",
            str(evidence/"20261111_recon_host.txt")+": old stream layout and physical cursor predicate",
        ],
        "central_validation": {
            "command": "python scripts/build/run_emulator_audit.py --build --package",
            "host_only_command": "python scripts/analysis/run_emulator_host_audit.py",
            "precondition": "Main chat builds independent core/pixel artifacts once after all groups freeze; then run bundle from recovered.",
            "new_unexecuted_compile_tests": [
                "python scripts/analysis/test_emulator_document_media_io.py",
                "python scripts/analysis/test_emulator_glyph_contract.py",
                "python scripts/analysis/test_android_movie_compile.py",
            ],
            "must_rerun": [
                "test_audio_remaining.py (corrected Reconquista 906 cases already separately passed)",
                "test_audio_completion.py (corrected Reconquista 120 cases already separately passed)",
                "test_voice_settings.py (2202 comparisons zero differences but global source changed concurrently)",
                "test_platform_control_semantics.py (kiraoto screen oracle updated by image group)",
                "test_native_slot_source.py (stale independent pixel receipt correctly blocked execution)",
                "test_native_import_resume.py --sample narci_himeko and --sample narci_onetwo",
                "test_android_audio_lifecycle.py; test_media_session_owner.py; test_android_dialogs.py",
            ],
        },
        "cross_group_requests": [
            {"owner": "main", "status": "observed_integrated",
             "detail": "EngineFeatures/engine.lua now expose dialog warning-retry 53 and literal cursor sentinel; pass-through is complete."},
            {"owner": "graphics", "status": "needs_central_recheck",
             "detail": "kiraoto original_screen double converter fixture failed in 17th platform sample; source changed since that attempt."},
            {"owner": "main", "status": "observed_integrated",
             "detail": "scripts/build/run_emulator_audit.py imports all HOST_SUITES; new regressions are included automatically. README belongs to main."},
        ],
        "limitations": [
            "No Android device execution, installation, publishing or real decoder audibility/Surface validation.",
            "Native Windows import remains explicitly limited to verified idle root, neutral scene and empty operand stack; arbitrary scene/local imports are rejected.",
            "Passing original wrapper/transport fixtures do not establish whole-game or hardware parity.",
            "Some legacy tests exercise both runtimes; only cases with player/runtime bindings count here.",
            "Reports carrying source_changed_during_test=true are interim evidence, not final acceptance.",
        ],
        "classification": {
            "scripts": "scripts/analysis/",
            "logs": str(evidence),
            "fixture_outputs": str(ROOT/"archive/2026-11/20261111_emulator_audit_host"),
            "note": "Legacy fixtures require recovered-relative report paths, so their outputs use recovered/archive; runner logs and new evidence use workspace archive.",
            "readme": "No new top-level directory; both dated archive subdirectories fit existing README classification.",
            "translations_modified": False, "game_assets_modified": False,
            "game_backups_created": False, "user_saves_accessed": False, "device_accessed": False,
        },
    }
    write_json(ROOT/"reports/emulator_audit_host.json", report)
    print("Host audit: source frozen;", len(files), "owned files inventoried;", len(FIXES), "fix groups.")


if __name__ == "__main__":
    main()
