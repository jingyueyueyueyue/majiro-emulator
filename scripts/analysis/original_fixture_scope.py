"""Keep unsupported original-EXE fixtures visible while testing other builds.

These helpers never change production engine selection or turn an incomplete
comparison suite into a passing exit status.
"""


def profile_for(profiles, sample, report, required=("numeric",)):
    report["requested_sample_count"] = report.get("requested_sample_count", 0) + 1
    result = profiles["executables"].get(sample["exe_sha256"])
    if result is not None:
        return result
    from original_comparison_profiles import recover
    if "path" in sample:
        path = sample["path"]
    else:
        from port_common import ROOT, read_json
        path = next(s["path"] for s in read_json(ROOT / "reports/opcode_dispatch.json")["samples"]
                    if s["exe_sha256"] == sample["exe_sha256"])
    evidence = recover(path, sample["exe_sha256"], sample["sample"])
    report.setdefault("comparison_only_profiles", []).append(evidence)
    result = evidence["profile"]
    missing = [key for key in required if key not in result]
    if not missing:
        return result
    known = next((row for row in profiles.get("unsupported_fixtures", [])
                  if row.get("sample") == sample["sample"]), None)
    boundary = {"sample": sample["sample"], "exe_sha256": sample["exe_sha256"],
                "status": "unsupported_fixture",
                "reason": (known or {}).get("error", "no verified original comparison profile"),
                "missing_comparison_fields": missing,
                "runtime_behavior_verified": False}
    report.setdefault("unsupported_fixtures", []).append(boundary)
    print(sample["sample"] + ": unsupported original fixture; continuing other builds", flush=True)
    return None


def finish_scope(report):
    report["verified_sample_count"] = sum(not s.get("unsupported_fixture_cases") for s in report["samples"])
    report["partial_sample_count"] = sum(bool(s.get("unsupported_fixture_cases")) for s in report["samples"])
    report["unsupported_sample_count"] = len({row["sample"] for row in report.get("unsupported_fixtures", [])})
    report["unsupported_fixture_case_count"] = len(report.get("unsupported_fixtures", []))
    if report.get("unsupported_fixtures") and report["status"] == "passed":
        report["status"] = "incomplete"
    report["incomplete_scope_is_not_passed"] = True
