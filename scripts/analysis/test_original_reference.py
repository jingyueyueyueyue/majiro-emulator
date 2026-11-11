"""Validate initialized guest provenance, rejected proofs and actual raw-identity execution."""
from pathlib import Path
import hashlib
import struct
import sys

sys.path[:0] = [str(Path(__file__).resolve().parents[1] / name) for name in ("convert", "build")]
from port_common import ROOT, archive_dir, read_json, write_json, timestamp
from original_reference import comparison_bytes, RAW_ID, INITIALIZED_ID
from original_opcode import OpcodeOracle
from run_background_compatibility import source_identity


def main():
    before = source_identity()
    folder = archive_dir("original_reference")
    sample = next(s for s in read_json(ROOT / "reports/opcode_dispatch.json")["samples"]
                  if s["exe_sha256"] == RAW_ID)
    raw = Path(sample["path"]).read_bytes()
    initialized, provenance = comparison_bytes(sample, raw)
    assert hashlib.sha256(initialized).hexdigest() == INITIALIZED_ID
    assert provenance["status"] == "validated_initialized_guest"
    cases = [{"case": "validated_original_bytes", "status": "passed"}]
    unchanged, missing = comparison_bytes(sample, raw, folder / "missing.json")
    assert unchanged == raw and missing["status"] == "initialized_reference_unavailable"
    cases.append({"case": "missing_reference_keeps_explicit_boundary", "status": "passed"})
    wrapper = read_json(ROOT / "reports/happy_wrapper_recovery.json")
    for name, edits in (
        ("forged_hash", {"initialized_reference_sha256": "0" * 64}),
        ("missing_native_proof", {"native_block_comparisons": 0}),
        ("unvalidated_range", {"modified_bytes": 4097}),
        ("outside_archive", {"initialized_reference": str(ROOT / "unapproved.exe")}),
    ):
        path = folder / (name + ".json")
        write_json(path, wrapper | edits)
        try:
            comparison_bytes(sample, raw, path)
        except ValueError as error:
            cases.append({"case": name, "status": "rejected", "reason": str(error)})
        else:
            raise AssertionError("invalid original reference accepted: " + name)
    oracle = OpcodeOracle(sample)
    assert oracle.reference_provenance["raw_exe_sha256"] == sample["exe_sha256"]
    assert oracle.reference_provenance["initialized_exe_sha256"] == INITIALIZED_ID
    result = oracle.execute("syscall", [(0, -4), (0, 3), (0, 7)],
                            struct.pack("<IH", 0x3D053317, 3), budget=20000)
    assert result == [(0, 3)], result
    cases.append({"case": "actual_initialized_minimum_from_raw_identity", "status": "passed",
                  "original_result": result})
    assert source_identity() == before
    report = {
        "status": "passed", "timestamp": timestamp(), "source_identity": before,
        "source_unchanged": True, "cases": cases, "reference_provenance": provenance,
        "device_accessed": False, "production_configuration_modified": False,
        "scope": "original comparison guest integrity and actual raw-identity native minimum; no game startup claimed",
        "evidence": str(folder.relative_to(ROOT)),
    }
    write_json(folder / "report.json", report)
    write_json(ROOT / "reports/original_reference.json", report)
    print(len(cases), "original reference validation/native checks passed")


if __name__ == "__main__":
    main()
