"""Preserve outer dispatch ambiguity through embedded-PE fallback and grouping."""
from pathlib import Path
import struct
import subprocess
import sys

import pefile

sys.path[:0] = [str(Path(__file__).resolve().parents[1] / "convert")]
from lua_bridge import create_lua, table_value
from port_common import ROOT, read_json, sha256, timestamp, write_json


def main(classes=None, java=None):
    folder = ROOT / "archive/2026-11/20261111_emulator_audit_profiles/20261111_phase4" / (
        timestamp().replace(":", "").replace("+", "_") + "_formal")
    folder.mkdir(parents=True, exist_ok=False)
    source = ROOT / "player/android/src/org/majiro/player/EngineFeatures.java"
    engine = ROOT / "player/runtime/majiro_engine.lua"
    fixture = ROOT / "scripts/analysis/EngineFeaturesChecks.java"
    before = {str(p.relative_to(ROOT)): sha256(p) for p in (source, engine, fixture)}
    java = java or Path(read_json(ROOT / "reports/android_toolchain.json")["java"]) / "bin"

    def command(name, arguments):
        result = subprocess.run(arguments, capture_output=True, timeout=60)
        (folder / ("20261111_" + name + ".log")).write_bytes(result.stdout + result.stderr)
        assert result.returncode == 0, (name, result.stderr.decode("utf8", "replace"))
        return result.stdout.decode("utf8").splitlines()

    reused = classes is not None
    if classes is None:
        classes = folder / "classes"
        classes.mkdir()
        command("compile", [str(java / "javac.exe"), "-encoding", "UTF-8", "-d", str(classes),
                            str(source), str(fixture)])
    samples = read_json(ROOT / "reports/opcode_dispatch.json")["samples"]
    by_name = {s["sample"]: s for s in samples}
    wrapper = read_json(ROOT / "reports/happy_wrapper_recovery.json")
    originals = [Path(s["path"]) for s in samples] + [
        Path(wrapper["original_container"]), Path(wrapper["initialized_reference"])]
    original_index = {s["sample"]: i for i, s in enumerate(samples)}
    raw = Path(by_name["idol_revolution"]["path"]).read_bytes()
    embedded = Path(by_name["kizoku"]["path"]).read_bytes()
    pe = pefile.PE(data=raw)
    text = next(s for s in pe.sections if s.Name.rstrip(b"\0") == b".text")
    offset = text.PointerToRawData + text.Misc_VirtualSize
    address = pe.OPTIONAL_HEADER.ImageBase + text.VirtualAddress + text.Misc_VirtualSize
    assert text.SizeOfRawData - text.Misc_VirtualSize >= 32
    entries = next(s["entries"] for s in read_json(ROOT / "reports/syscall_dispatch.json")["samples"]
                   if s["sample"] == "idol_revolution")
    target = next(e["selected_va"] for e in entries if e["hash"] == "efd49bc9")
    duplicate = b"\x3d" + struct.pack("<I", 0x45815f6d) + b"\x0f\x84" + struct.pack("<i", target - address - 11)
    ambiguous = bytearray(raw)
    ambiguous[offset:offset + 11] = duplicate
    struct.pack_into("<I", ambiguous, text.get_file_offset() + 8, text.Misc_VirtualSize + 16)
    absent = bytearray(raw)
    removed = 0
    for at in range(text.PointerToRawData, text.PointerToRawData + text.Misc_VirtualSize - 6):
        immediate = at + 1 if raw[at] == 0x3d else at + 2 if raw[at] == 0x81 and raw[at + 1] >= 0xf8 else None
        if immediate is not None and struct.unpack_from("<I", raw, immediate)[0] == 0x45815f6d:
            struct.pack_into("<I", absent, immediate, 0x1234abcd)
            removed += 1
    assert removed, "original string comparator dispatch was not found"
    names = ("outer_ambiguous", "outer_ambiguous_embedded", "outer_absent_embedded", "outer_clean_embedded")
    generated = []
    checks = []

    def check(name, condition):
        assert condition, name
        checks.append({"name": name, "status": "passed"})

    try:
        for name, data in zip(names, (ambiguous, ambiguous + embedded, absent + embedded, raw + embedded)):
            path = folder / ("20261111_" + name + ".exe")
            path.write_bytes(data)
            generated.append(path)
        # Repeat the original inputs after all counterexamples in the same
        # JVM: fallback/ambiguity state must not leak into a later executable.
        paths = originals + generated + originals
        listing = folder / "20261111_inputs.txt"
        listing.write_text("\n".join(str(p) for p in paths) + "\n", encoding="utf8")
        lines = command("features", [str(java / "java.exe"), "-cp", str(classes),
                        "org.majiro.player.EngineFeaturesChecks", str(listing)])
        assert len(lines) == len(paths)
        count = len(originals)
        base = [line.split(",") if line else [] for line in lines[:count]]
        observed = {name: line.split(",") for name, line in zip(names, lines[count:count + 4])}
        idol = base[original_index["idol_revolution"]]
        kizoku = base[original_index["kizoku"]]
        marker = "engine:ambiguous-dispatch:45815f6d"
        check("all_19_original_outputs_stable_after_counterexamples",
              lines[:count] == lines[count + 4:] and
              all("engine:api-dispatch" in row and not any(
                  item.startswith("engine:ambiguous-dispatch") for item in row) for row in base))
        check("outer_ambiguity_survives_embedded_payload", marker in observed["outer_ambiguous_embedded"])
        check("outer_diagnostic_independent_of_embedded_payload",
              observed["outer_ambiguous"] == observed["outer_ambiguous_embedded"])
        check("genuinely_absent_dispatch_still_falls_back", observed["outer_absent_embedded"] == kizoku)
        check("valid_outer_dispatch_keeps_its_features", observed["outer_clean_embedded"] == idol)
        lua = create_lua()
        detector = lua.execute(engine.read_text("utf8"))

        def profile(features):
            return table_value(detector["detect"](lua.table_from({
                "family": "Majiro", "archive_version": 2, "script_signature": "MajiroObjX1.000",
                "features": features}, recursive=True)))

        def grouped(*groups):
            return [item for group in groups for item in ("engine-source:begin", *group, "engine-source:end")]

        bad = observed["outer_ambiguous_embedded"]
        for label, features in (
            ("direct", bad), ("single_group", grouped(bad)),
            ("first_group", grouped(bad, idol)), ("last_group", grouped(idol, bad))):
            try:
                profile(features)
            except Exception as error:
                check("reject_ambiguity_" + label, "ambiguous Majiro syscall dispatch 45815f6d" in str(error))
            else:
                raise AssertionError("outer ambiguity was silently accepted: " + label)
        check("legitimate_fallback_profile_matches_embedded",
              profile(observed["outer_absent_embedded"]) == profile(kizoku))
        check("valid_outer_profile_and_ultra_unchanged",
              profile(observed["outer_clean_embedded"]) == profile(idol) and
              profile(idol)["ultra_fast_mode_available"] is True)
        after = {str(p.relative_to(ROOT)): sha256(p) for p in (source, engine, fixture)}
        check("source_identity_stable", before == after)
        report = {"timestamp": timestamp(), "status": "passed", "checks": checks,
                  "check_count": len(checks), "original_inputs": count, "counterexample_inputs": len(generated),
                  "source_sha256": after, "sources_stable": before == after, "compiled_fixture_reused": reused,
                  "scope": "Formal Java detector and Lua selector; no archived source or candidate is loaded.",
                  "evidence": str(folder.relative_to(ROOT)), "device_accessed": False,
                  "original_game_modified": False, "full_apk_or_native_build": False}
        write_json(folder / "20261111_report.json", report)
        write_json(ROOT / "reports/engine_embedded_ambiguity_tests.json", report)
    finally:
        for path in generated:
            path.unlink(missing_ok=True)
    print(f"Embedded engine ambiguity: {len(checks)} checks; {len(originals)} original inputs; formal code passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
