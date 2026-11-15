"""Preserve every native byte unit through VM strings, hashes and saved state."""
from pathlib import Path
import json
import sys
import zlib

sys.path[:0] = [str(Path(__file__).resolve().parents[1] / part)
                for part in ("analysis", "convert")]
from lua_bridge import create_lua
from port_common import ROOT, archive_dir, timestamp, write_json


def main():
    folder = archive_dir("native_string_bytes")
    lua = create_lua()
    strings = lua.execute((ROOT / "player/runtime/majiro_strings.lua").read_text("utf8"))
    codec = lua.execute((ROOT / "player/runtime/majiro_codec.lua").read_text("utf8"))
    decode = lua.eval("""function(convert)return function(raw)
      return convert((raw:gsub('.',function(c)return string.format('%02x',c:byte())end)))
    end end""")(lambda text: bytes.fromhex(text).decode("cp932"))
    encode = strings["native_encoder"](lambda text: text.encode("cp932"))
    native_decode = strings["native_decoder"](decode, lambda text: text.encode("cp932"))
    check = lua.eval("""function(decode,encode)
      local function test(raw)assert(encode(decode(raw))==raw,'native byte unit changed')end
      for a=0,255 do
        test(string.char(a))
        for b=0,255 do test(string.char(a,b))end
      end
      return 256+65536
    end""")
    units = check(native_decode, encode)
    # Real Kiraoto system-save path: UTF-8 bytes mixed into a native CP932 string.
    raw = bytes.fromhex("3c6c6566743e5c5c61727572755ce381ade381933137e4bd9ce79bae5c40"
                        "e5ae9fe8a18ce792b0e5a2835c66617374646174613c2f6c6566743e2a")
    from_hex = lua.eval("""function(decode,hex)return decode((hex:gsub('..',
      function(c)return string.char(tonumber(c,16))end)))end""")
    to_hex = lua.eval("""function(encode,text)return(encode(text):gsub('.',
      function(c)return string.format('%02x',c:byte())end))end""")
    text = from_hex(native_decode, raw.hex())
    assert to_hex(encode, text) == raw.hex()
    providers = lua.table()
    options = lua.table_from({"cp932": lambda text: text.encode("cp932")})
    installed = strings["install"](lambda name, fn: providers.__setitem__(name, fn), lua.table(), options)
    assert installed["hash"](text) == zlib.crc32(raw)
    restored = codec["decode"](codec["encode"](lua.table_from({"path": text})))["path"]
    assert to_hex(encode, restored) == raw.hex()
    chars = list(installed["characters"](text).values())
    rebuilt = "".join(installed["slice"](text, i, 1) for i in range(len(chars)))
    assert to_hex(encode, rebuilt) == raw.hex()
    assert installed["compare"](text, restored, True) == 0
    assert providers["$strleft$"](None, lua.table_from([text, 6])) == "<left>"
    assert to_hex(encode, installed["fold"](text)) == raw.lower().hex()
    assert installed["compare"](from_hex(native_decode, "81"), from_hex(native_decode, "82"), True) < 0
    report = {"status": "passed", "timestamp": timestamp(), "native_byte_units": units,
              "real_mixed_path_roundtrip": True, "hash_matches_original_bytes": True,
              "portable_save_and_character_operations": True,
              "evidence": str(folder.relative_to(ROOT))}
    write_json(folder / "report.json", report)
    write_json(ROOT / "reports/native_string_bytes_tests.json", report)
    print("Native strings:", json.dumps(report))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
