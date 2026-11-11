"""Check production CRC32 against independent byte results and measure repeat work."""
from pathlib import Path
import random
import statistics
import sys
import time
import zlib

sys.path[:0] = [str(Path(__file__).resolve().parents[1] / "convert")]
from port_common import ROOT, archive_dir, sha256, timestamp, write_json
from lua_bridge import create_lua


def main():
    source = ROOT / "player/runtime/majiro_strings.lua"
    lua = create_lua()
    module = lua.execute(source.read_text("utf8"))
    providers = lua.table()
    define = lua.eval("function(p)return function(n,f)p[n]=f end end")(providers)
    encoding = ["cp932"]
    options = lua.table_from({"cp932": lambda text: text.encode(encoding[0]), "string_case": "cp932"})
    api = module["install"](define, lua.table(), options)
    rng = random.Random(20261111)
    alphabet = "AaZz09_/@- " + "\u3042\u30bd\uff21\uff41\u0391\u03b1\uff76"
    strings = ["", "A\0ignored", "\0", "123456789"]
    strings += ["".join(rng.choice(alphabet) for _ in range(rng.randrange(1, 160))) for _ in range(600)]
    strings += ["x" * size for size in (255, 256, 257, 4096)]
    for text in strings + strings[:300]:
        assert api["hash"](text) == zlib.crc32(text.split("\0", 1)[0].encode("cp932")), repr(text[:30])
    # Reusing a display string after changing byte encoding must recompute,
    # and failed strict encodings must not be hidden by an earlier cache hit.
    for mode in ("cp932", "utf8", "ascii"):
        encoding[0] = mode
        if mode == "ascii":
            try:
                api["hash"]("\u3042")
            except Exception:
                pass
            else:
                raise AssertionError("strict encoder failure was hidden")
        else:
            assert api["hash"]("\u3042") == zlib.crc32("\u3042".encode(mode))
    encoding[0] = "cp932"
    raw = lua.eval(r"""function(mod)
      return mod.install(function()end,{},{}).hash
    end""")(module)
    assert raw("123456789") == 0xcbf43926
    # Exercise all one-byte values through a byte-safe Lua result, including
    # zero termination, without crossing Python's UTF-8 string boundary.
    byte_results = lua.eval("function(hash)local r={}for i=0,255 do r[i+1]=hash(string.char(i))end return r end")(raw)
    for byte in range(256):
        assert byte_results[byte + 1] == zlib.crc32(b"" if byte == 0 else bytes([byte]))
    benchmark = lua.eval("""function(hash,rounds,unique)
      local total=0
      for i=1,rounds do total=total+hash('CONSOLE_EVENT_'..(unique and i or i%32))end
      return total
    end""")
    baseline = ROOT / "archive/2026-11/20261111_emulator_audit_core/20261111_strings_before_crc_optimization.lua"
    measurements = {}
    if baseline.is_file():
        old_module = lua.execute(baseline.read_text("utf8"))
        old = lua.eval("function(mod)return mod.install(function()end,{},{}).hash end")(old_module)
        for unique in (False, True):
            costs = {}
            for label, hash_function in (("before", old), ("after", raw)):
                values = []
                for _ in range(3):
                    began = time.perf_counter()
                    benchmark(hash_function, 2000, unique)
                    values.append(time.perf_counter() - began)
                costs[label] = statistics.median(values)
            costs["speedup"] = costs["before"] / costs["after"]
            measurements["unique" if unique else "repeated"] = costs
    report = {"timestamp": timestamp(), "status": "passed", "cases": len(strings) + 300 + 259,
              "source_sha256": sha256(source), "encoding_change_safe": True,
              "cache_entries_maximum": 256, "cache_key_bytes_maximum": 256,
              "benchmarks_seconds": measurements, "device_accessed": False}
    folder = archive_dir("player_hashing")
    write_json(folder / "report.json", report)
    write_json(ROOT / "reports/player_hashing_tests.json", report)
    print("Hash checks passed:", report["cases"], "measurements:", measurements)


if __name__ == "__main__":
    main()
