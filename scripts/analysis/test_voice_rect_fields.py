"""Compare independent history voice fields against actual original getters."""
from pathlib import Path
import struct
import sys

sys.path[:0] = [str(Path(__file__).resolve().parents[1] / "convert")]
from lua_bridge import create_lua
from original_strings import StringOracle
from port_common import ROOT, archive_dir, read_json, timestamp, write_json


def main():
    sample = next(s for s in read_json(ROOT / "reports/opcode_dispatch.json")["samples"]
                  if s["sample"] == "kiraoto")
    entries = next(s for s in read_json(ROOT / "reports/syscall_dispatch.json")["samples"]
                   if s["sample"] == "kiraoto")["entries"]
    oracle = StringOracle(sample)
    pairs = (("voice_a", ""), ("voice_b", "voice_c"), ("", ""),
             ("", "additional"), ("primary", "second,third"))
    rows = []
    for index, (primary, additional) in enumerate(pairs):
        # These two independent 260-byte buffers are in each original
        # 532-byte voice record; only input records are initialized here.
        for base, text in ((0x4cd254, primary), (0x4cd358, additional)):
            data = text.encode("cp932")
            oracle.machine.mem_write(base + index * 532, data + bytes(260 - len(data)))
        row = {"primary": primary, "additional": additional}
        for name, expected in (("$voicerect_name$", primary),
                               ("$voicerect_name_alt$", additional)):
            entry = next(e for e in entries if e.get("name") == name)
            result = oracle.execute("syscall", [(0, index)],
                                    struct.pack("<IH", int(entry["hash"], 16), 1),
                                    budget=200000)
            assert len(result) == 1 and result[0][0] == 2
            actual = oracle.string_result(*result[0])
            assert actual == expected, (name, actual, expected)
            row[name] = actual
        rows.append(row)
    for runtime in ("player/runtime", "runtime"):
        lua = create_lua()
        providers, state = lua.table(), lua.table()
        define = lua.eval("function(p)return function(name,f)p[name]=f end end")(providers)
        module = lua.execute((ROOT / runtime / "majiro_console.lua").read_text(encoding="utf8"))
        console, _ = module["install"](define, state, lua.table(), lambda *args: None)
        console["voice_rects"] = lua.table_from(
            [{"name": primary, "alt_name": additional} for primary, additional in pairs],
            recursive=True)
        for index, row in enumerate(rows):
            for name in ("$voicerect_name$", "$voicerect_name_alt$"):
                actual = providers[name](None, lua.table_from([index]))
                assert actual == row[name], (runtime, name, index, actual, row[name])
        assert providers["$voicerect_num"](None, lua.table()) == len(rows)
        assert providers["$voicerect_name_alt$"](None, lua.table_from([len(rows)])) == ""
    folder = archive_dir("voice_rect_fields")
    report = {"status": "passed", "timestamp": timestamp(), "cases": rows,
              "original_getter_comparisons": len(rows) * 2,
              "original_exe_sha256": sample["exe_sha256"],
              "runtime_copies": ["player/runtime", "runtime"], "device_accessed": False,
              "scope": "primary/additional field ownership; full multi-voice history capture is not asserted"}
    write_json(folder / "report.json", report)
    write_json(ROOT / "reports/voice_rect_fields.json", report)
    print("History voice fields:", len(rows) * 2,
          "actual original getters and both shared VMs passed")


if __name__ == "__main__":
    main()
