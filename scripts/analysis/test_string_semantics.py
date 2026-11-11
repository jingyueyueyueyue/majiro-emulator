"""Cross-build string bytecode and syscall comparisons with initialized CP932."""
from __future__ import annotations

from collections import Counter
import hashlib
import itertools
import json
from pathlib import Path
import struct
import sys

from audit_player_semantics import registered_providers, source_identity
from original_strings import StringOracle
from original_opcode import native_number
from original_fixture_scope import profile_for, finish_scope

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "convert"))
from port_common import ROOT, archive_dir, timestamp, write_json

TEXTS = ("", "A", "a", "ab", "az", "Z", "0", "a/b", "a\\b",
         "\uff21", "\uff41", "\u0391", "\u03b1", "\u0410", "\u0430",
         "\u3042", "\u30bd", "\uff76", "\u3000", "A\u30bd", "a\u30bd",
         "A\0BC", "\0", "\0A")


def syscall_cases(name):
    if name in ("$strcmp", "$stricmp", "$strstr", "$strmatch"):
        return itertools.product(TEXTS, repeat=2)
    if name in ("$strlen", "$num_strsplit", "$strupr$", "$strlwr$", "$atoi", "$crc32str"):
        values = (*TEXTS, '  "one", two, , "three"  ', "1,2,3", ",,",
                  " -123x", "+2147483648", "9999999999999999999999")
        return [(text,) for text in values]
    if name in ("$strleft$", "$strright$"):
        return itertools.product(("", "ABCDE", "A\u3042B\u30bd", "\uff21\uff22\uff23", "A\0BC"), (-3, -1, 0, 1, 2, 5, 20))
    if name == "$strmid$":
        return itertools.product(("", "ABCDE", "A\u3042B\u30bd", "A\0BC"), (-2, 0, 1, 5), (-2, 0, 1, 3, 10))
    if name == "$strsplit$":
        return itertools.product(("A,B", " , ,C, ", '"A B",  "C"', "A\u3000,B\u3000", "A\0,B"), (-1, 0, 1, 2, 8))
    return []


def run():
    samples = json.loads((ROOT/"reports/opcode_dispatch.json").read_text(encoding="utf-8"))["samples"]
    calls = json.loads((ROOT/"reports/syscall_dispatch.json").read_text(encoding="utf-8"))["samples"]
    calls = {row["sample"]: row for row in calls}
    profiles = json.loads((ROOT/"config/player_engine_profiles.json").read_text(encoding="utf-8"))
    before = source_identity()
    report = {"schema": 1, "timestamp": timestamp(), "source_identity": before,
              "samples": [], "failures": [], "complete_engine_semantics": False,
              "scope": "CP932 strings, comparison order/case, slicing, lookup, parsing and case conversion",
              "host_boundaries": ["single-thread CRT locks", "bounded guest heap",
                                  "real Windows NLS character APIs with explicit CP932"],
              "vm_sha256": before["files"]["player/runtime/majiro_vm.lua"]}
    for sample in samples:
        oracle = StringOracle(sample)
        profile = profile_for(profiles, sample, report, required=(
            "numeric", "minmax_arguments", "string_case", "integer_parse", "empty_search_absent"))
        if profile is None:
            continue
        search_entry = next(entry for entry in calls[sample["sample"]]["entries"]
                            if entry["name"] == "$strstr")
        search_probe = oracle.execute("syscall", [(2, oracle.string("a", 1)),
                                                  (2, oracle.string("A", 0))],
                                      struct.pack("<IH", int(search_entry["hash"], 16), 2),
                                      budget=200000)
        search_sensitive = search_probe == [(0, 0xffffffff)]
        assert search_probe in ([(0, 0)], [(0, 0xffffffff)])
        lua, _, _, system = registered_providers(profile["minmax_arguments"], profile["string_case"],
                                                  profile["integer_parse"], profile["empty_search_absent"],
                                                  overrides={"string_search_case_sensitive":
                                                      search_sensitive})
        module = lua.execute((ROOT/"player/runtime/majiro_vm.lua").read_text(encoding="utf-8"))
        opcodes = lua.execute((ROOT/"player/tables/opcodes.lua").read_text(encoding="utf-8"))
        execute = lua.eval("""
        function(mod,ops,system,opcode,hash,args,discard,profile,constant)
          local row={0,opcode}
          if hash then row={0,discard and 0x835 or 0x834,hash,#args}
          elseif constant then row[3]=args[1]end
          local script={name='string-fixture',main=0,functions={},
            code={row,{10000,0x800,0},{10006,0x82b}}}
          local vm=mod.new{opcodes=ops,loader=function()return script end,
            syscall=system.invoke,string_ops=system.strings,numeric_profile=profile}
          vm:begin(script.name)
          if hash then for i=#args,1,-1 do vm:push(args[i])end
          elseif not constant then for _,value in ipairs(args)do vm:push(value)end end
          local ok,why=pcall(function()vm:step()end)
          return ok,why,vm.stack
        end
        """)
        counts, failures = Counter(), Counter()
        call_counts, call_failures = Counter(), Counter()
        arities = {}

        def check(name, args, entry=None, discard=False):
            counter, errors = (call_counts, call_failures) if entry else (counts, failures)
            counter[name] += 1
            values = [(2, oracle.string(value, i)) if isinstance(value, str) else (0, value & 0xFFFFFFFF)
                      for i, value in enumerate(args)]
            hash_ = int(entry["hash"], 16) if entry else None
            constant = name == "ldstr"
            operands = b""
            opcode_name = name
            if entry:
                opcode_name = "syscallp" if discard else "syscall"
                operands = struct.pack("<IH", hash_, len(args))
                values.reverse()
            elif constant:
                data = args[0].encode("cp932") + b"\0"
                operands = struct.pack("<H", len(data)) + data
                values = []
            reason = None
            try:
                expected = oracle.execute(opcode_name, values, operands, budget=200000)
                expected = [oracle.string_result(*value) if value[0] == 2 else native_number(*value)
                            for value in expected]
                ok, why, actual = execute(module, opcodes, system,
                                          int(oracle.rows[opcode_name]["opcode"], 16),
                                          hash_, lua.table_from(args), discard, profile["numeric"], constant)
                if not ok:
                    reason = str(why)
                elif len(actual) != len(expected) or any(actual[i] != value for i, value in enumerate(expected, 1)):
                    reason = f"result {[actual[i] for i in range(1,len(actual)+1)]!r} != {expected!r}"
            except Exception as error:
                reason = str(error)
            if reason:
                errors[name] += 1
                if errors[name] <= 10:
                    report["failures"].append({"sample": sample["sample"], "name": name,
                                               "args": args, "discard": discard, "reason": reason})

        for name in ("cle.s", "clt.s", "cge.s", "cgt.s", "ceq.s", "cne.s"):
            if name not in oracle.rows:
                report.setdefault("absent_original_opcodes", []).append(
                    {"sample": sample["sample"], "opcode": name})
                continue
            for args in itertools.product(TEXTS, repeat=2):
                check(name, args)
        for name in ("ldstr", "add.s"):
            for args in [(text,) for text in TEXTS] if name == "ldstr" else itertools.product(TEXTS[:9], repeat=2):
                check(name, args)
        wanted = {"$strcmp", "$stricmp", "$strlen", "$strstr", "$strmatch",
                  "$strleft$", "$strright$", "$strmid$", "$strsplit$", "$num_strsplit",
                  "$strupr$", "$strlwr$", "$atoi", "$crc32str"}
        entries = {}
        for entry in calls[sample["sample"]]["entries"]:
            name = entry["name"]
            if name not in wanted:
                continue
            entries[name] = entry
            for args in syscall_cases(name):
                arities.setdefault(name, set()).add(len(args))
                check(name, args, entry)
            args = next(iter(syscall_cases(name)))
            check(name, args, entry, True)
        row = {"sample": sample["sample"], "exe_sha256": sample["exe_sha256"],
               "cases": dict(counts), "failures": dict(failures),
               "passed_opcodes": sorted(name for name in counts if not failures[name]),
               "calls": [{"hash": entries[name]["hash"], "name": name, "cases": call_counts[name],
                          "failures": call_failures[name], "status": "passed" if not call_failures[name] else "failed",
                          "argument_counts": sorted(arities[name]), "discard_modes": [False, True]}
                         for name in sorted(call_counts)],
               "nls_api_calls": dict(oracle.api_calls), "guest_allocations": oracle.allocations,
               "crt_initialization": oracle.crt_initialization, "string_case": profile["string_case"]}
        report["samples"].append(row)
        print(f"{sample['sample']}: {sum(counts.values())+sum(call_counts.values())} string comparisons; "
              f"{sum(failures.values())+sum(call_failures.values())} mismatches", flush=True)
    report["comparisons"] = sum(sum(row["cases"].values())+sum(call["cases"] for call in row["calls"])
                                for row in report["samples"])
    report["mismatches"] = sum(sum(row["failures"].values())+sum(call["failures"] for call in row["calls"])
                              for row in report["samples"])
    report["source_changed_during_test"] = source_identity() != before
    report["test_sources"] = {name: hashlib.sha256((ROOT/"scripts/analysis"/name).read_bytes()).hexdigest()
                              for name in ("original_opcode.py", "original_strings.py", "original_fixture_scope.py",
                                           "original_comparison_profiles.py", "test_string_semantics.py")}
    report["status"] = "passed" if not report["mismatches"] and not report["source_changed_during_test"] else "failed"
    finish_scope(report)
    folder = archive_dir("string_semantics")
    write_json(folder/(folder.name[:8]+"_string_semantics.json"), report)
    write_json(ROOT/"reports/string_semantics_tests.json", report)
    return 0 if report["status"] == "passed" else 1


if __name__ == "__main__":
    raise SystemExit(run())
