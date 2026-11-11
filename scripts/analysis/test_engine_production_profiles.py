"""Compare Java -> Lua production configuration with original executable behavior."""
from pathlib import Path
import json
import subprocess
import sys

sys.path[:0] = [str(Path(__file__).resolve().parents[1] / "convert")]
from port_common import ROOT, read_json, sha256, timestamp, write_json
from lua_bridge import create_lua, table_value
from original_comparison_profiles import recover
from original_events import EventOracle
from original_screen import ScreenOracle
from original_text import TextOracle
from original_strings import StringOracle
from original_array_layout import legacy_3d_stride
from original_extsound import ExtSoundOracle
from original_platform_controls import PlatformOracle
from test_external_checks import LicenseOracle
from inspect_engine_feature_paths import EVIDENCE
import struct


def production_profile(features, archive):
    lua = create_lua()
    host = lua.table()
    host["movies"] = lambda: lua.table()
    host["identity"] = lambda: "a" * 64
    host["asset_text"] = lambda name: (ROOT / "player" / name).read_text("utf8")
    host["member_names"] = lambda: lua.table_from(["fixture.env"])
    host["resource_text"] = lambda name: "CLIENT_WIDTH 800\nCLIENT_HEIGHT 600\n"
    host["engine_requirements"] = lambda: lua.table_from({
        "family": "Majiro", "archive_version": archive,
        "script_signature": "MajiroObjX1.000", "features": features}, recursive=True)
    host["engine_hashes"] = lambda: (_ for _ in ()).throw(AssertionError("EXE allowlist queried"))
    lua.globals()["HOST"] = host
    actual = table_value(lua.execute(
        (ROOT / "player/runtime/player_configuration.lua").read_text("utf8"))["make"]())
    actual["numeric"] = actual["numeric_profile"]
    # Requirements retain the source evidence; they are not behavior options.
    actual.pop("engine_requirements")
    return actual


def main():
    folder = EVIDENCE / (timestamp().replace(":", "").replace("+", "_") + "_production")
    folder.mkdir(parents=True, exist_ok=False)
    java = Path(read_json(ROOT / "reports/android_toolchain.json")["java"]) / "bin"
    feature_source = ROOT / "player/android/src/org/majiro/player/EngineFeatures.java"
    sources = (feature_source, ROOT / "player/runtime/majiro_engine.lua",
               ROOT / "player/runtime/player_configuration.lua",
               ROOT / "player/runtime/player_boot.lua")
    before = {str(p.relative_to(ROOT)): sha256(p) for p in sources}
    checks = ROOT / "scripts/analysis/EngineFeaturesChecks.java"
    samples = read_json(ROOT / "reports/opcode_dispatch.json")["samples"]
    dispatch = {s["exe_sha256"]: s["entries"] for s in read_json(
        ROOT / "reports/syscall_dispatch.json")["samples"]}
    games = {g.get("id") or Path(g["directory"]).name: g for g in read_json(
        ROOT / "reports/majiro_samples.json")["games"]}
    expected_profiles = read_json(ROOT / "config/player_engine_profiles.json")["executables"]
    dialog_rows = read_json(ROOT / "reports/dialog_recovery_tests.json")["samples"]
    wrapped = read_json(ROOT / "reports/happy_wrapper_recovery.json")
    inputs = [Path(s["path"]) for s in samples]
    inputs += [Path(wrapped["original_container"]), Path(wrapped["initialized_reference"])]
    # Changes to unrelated PE resources/overlay must not change behavior hints.
    mutated = folder / "20261111_changed_resources.exe"
    mutated.write_bytes(inputs[0].read_bytes() + b"\0MajiroSavV1.105\0"
                        + b"\x3d\x9d\x70\xd1\x2b\x74\x00\0unrelated resource change\0")
    inputs.append(mutated)
    malformed = folder / "20261111_malformed.exe"
    malformed.write_bytes(b"MZ" + bytes(58) + b"\xff\xff\xff\x7fMajiroObj")
    inputs.append(malformed)
    listing = folder / "20261111_inputs.txt"
    listing.write_text("\n".join(str(p) for p in inputs) + "\n", encoding="utf8")

    def command(name, arguments):
        result = subprocess.run(arguments, capture_output=True, timeout=120)
        (folder / (name + ".txt")).write_bytes(result.stdout + result.stderr)
        if result.returncode:
            raise RuntimeError(name + " failed: " + result.stderr.decode("utf8", "replace"))
        return result.stdout.decode("utf8")

    command("compile", [str(java / "javac.exe"), "-encoding", "UTF-8", "-d", str(folder),
                        str(feature_source), str(checks)])
    output = command("run", [str(java / "java.exe"), "-cp", str(folder),
                             "org.majiro.player.EngineFeaturesChecks", str(listing)]).splitlines()
    assert len(output) == len(inputs), (len(output), len(inputs))
    features = [line.split(",") if line else [] for line in output]
    assert features[-2] == features[0], "resource-only edit changed runtime behavior"
    assert features[-1] == [], "malformed PE produced executable behavior hints"
    rows, failures = [], []
    for index, sample in enumerate(samples):
        entries = dispatch[sample["exe_sha256"]]
        profile = expected_profiles.get(sample["exe_sha256"])
        if profile is None:
            profile = recover(sample["path"], sample["exe_sha256"], sample["sample"])["profile"]
        expected = dict(profile)
        expected.pop("string_crt_initialization", None)
        expected = {key: value for key, value in expected.items() if value is not None}
        screen = PlatformOracle(sample, entries)
        expected["screen_controls"] = dict(ScreenOracle.profile(screen))
        expected["screen_controls"].setdefault("integer_conversion", "sse32")
        expected["platform_controls"] = screen.profile()
        if screen.has_virtual_cursor:
            expected["screen_controls"]["cursor_reset_sentinel"] = screen.cursor_reset_sentinel
        expected["text_controls"] = {"x_control_event": True, "history_suppresses_message": True,
                                     **TextOracle(sample, entries).profile()}
        events = EventOracle(sample, entries)
        expected["event_controls"] = {"release_registers_hook": events.release_registers_hook}
        expected["dialog_controls"] = dict(expected.get("dialog_controls", {}))
        dialog = next(row for row in dialog_rows if row["exe_sha256"] == sample["exe_sha256"])
        warning = next(call for call in dialog["calls"] if call["name"] == "$dialog_warning_retrycancel")
        assert len(warning["flags"]) == 1
        expected["dialog_controls"]["warning_retrycancel_flags"] = warning["flags"][0]
        strings = StringOracle(sample)
        search = next(e for e in entries if e["name"] == "$strstr")
        original = strings.execute("syscall", [(2, strings.string("a", 1)), (2, strings.string("A", 0))],
                                   struct.pack("<IH", int(search["hash"], 16), 2), budget=200000)
        assert original in ([(0, 0)], [(0, 0xffffffff)])
        expected["string_search_case_sensitive"] = original == [(0, 0xffffffff)]
        expected["array_3d_legacy_stride"] = legacy_3d_stride(strings)
        expected["audio_controls"] = ExtSoundOracle(sample, entries).audio_profile()
        if any(e["hash"] == "87a631a6" for e in entries):
            license_oracle = LicenseOracle(sample)
            license_oracle.phase = 1  # The modeled registry-open failure, without host access.
            result = license_oracle.execute("syscall", [(2, license_oracle.string("fixture", 0))],
                                            struct.pack("<IH", 0x87a631a6, 1), budget=300000)
            assert result in ([(0, 0)], [(0, 1)]), result
            expected["external_controls"] = {
                "license": "native_constant_zero" if result == [(0, 0)] else "cryptoapi_user_license"}
        game = games[sample["sample"]]
        archive = max(a["version"] for a in game["archives"])
        actual = production_profile(features[index], archive)
        differences = []

        def compare(original, configured, prefix=""):
            for key, value in original.items():
                if isinstance(value, dict):
                    compare(value, configured.get(key, {}) or {}, prefix + key + ".")
                elif configured.get(key) != value:
                    differences.append({"field": prefix + key, "original": value,
                                        "production": configured.get(key)})

        compare(expected, actual)
        # These checks require direct positive evidence so a matching generic
        # default cannot accidentally conceal a broken feature detector.
        required_features = [
            "engine:api-dispatch",
            "array:3d_legacy_stride" if expected["array_3d_legacy_stride"] else "array:3d_row_major",
            "audio:extensions:" + "-".join(x[1:] for x in expected["audio_controls"]["extensions"]),
            "text:x_event" if expected["text_controls"]["x_control_event"] else "text:x_no_event",
            "text:history_suppresses_message" if expected["text_controls"]["history_suppresses_message"]
            else "text:history_retains_message",
        ]
        if screen.has_virtual_cursor:
            required_features.append("screen:cursor_reset_sentinel" if screen.cursor_reset_sentinel
                                     else "screen:cursor_literal_coordinates")
        if expected["screen_controls"]["coordinate_mode"] == "double":
            required_features.append("screen:integer_conversion:" +
                                     expected["screen_controls"]["integer_conversion"])
        if expected.get("external_controls", {}).get("license") == "native_constant_zero":
            required_features.append("external:license:constant_zero")
        for feature in required_features:
            if feature not in features[index]:
                differences.append({"field": "required_feature", "original": feature, "production": None})
        row = {"sample": sample["sample"], "exe_sha256": sample["exe_sha256"], "features": features[index],
               "required_features": required_features,
               "original_controls": expected, "production_profile": {k: actual.get(k) for k in expected},
               "differences": differences, "status": "failed" if differences else "passed"}
        rows.append(row)
        failures.extend({"sample": sample["sample"], **difference} for difference in differences)
        print(sample["sample"], "production configuration differences:", len(differences), flush=True)
    happy = next(i for i, s in enumerate(samples) if s["sample"] == "happy_princess")
    wrapper_checks = []
    archive = max(a["version"] for a in games[samples[happy]["sample"]]["archives"])
    raw_profile = production_profile(features[happy], archive)
    for label, found in (("wrapper", features[-4]), ("initialized", features[-3])):
        configured = production_profile(found, archive)
        equal = configured == raw_profile
        wrapper_checks.append({"input": label, "same_behavior": equal,
                               "extra_features": sorted(set(found) - set(features[happy])),
                               "missing_features": sorted(set(features[happy]) - set(found))})
        if not equal:
            failures.append({"sample": label, "field": "wrapper_behavior",
                             "original": raw_profile, "production": configured})
    after = {str(p.relative_to(ROOT)): sha256(p) for p in sources}
    if before != after:
        failures.append({"field": "sources_changed_during_test", "original": before, "production": after})
    def leaves(value):
        return sum(leaves(v) for v in value.values()) if isinstance(value, dict) else 1
    report = {"timestamp": timestamp(), "status": "failed" if failures else "passed",
              "samples": rows, "failures": failures, "comparisons": sum(
                  leaves(r["original_controls"]) for r in rows), "sample_count": len(rows),
              "wrapper_and_initialized_agree": all(c["same_behavior"] for c in wrapper_checks),
              "wrapper_feature_evidence": wrapper_checks,
              "wrapper_note": "Only mapped readable code is inspected. The initialized reference restores "
                              "an encrypted first code page; extra explicit hints are retained in evidence "
                              "while the complete production behavior configuration must remain identical.",
              "resource_mutation_preserves_behavior": True,
              "invalid_pe_rejected": True, "device_accessed": False,
              "scope": "production Java feature discovery and Lua configuration, compared with unmodified original-code fixtures",
              "source_sha256": after, "sources_stable": before == after,
              "evidence": str(folder.relative_to(ROOT))}
    write_json(folder / "report.json", report)
    write_json(ROOT / "reports/engine_production_profiles.json", report)
    mutated.unlink()
    malformed.unlink()
    return int(bool(failures))


if __name__ == "__main__":
    raise SystemExit(main())
