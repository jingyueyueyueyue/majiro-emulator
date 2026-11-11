"""Strict independent reference for native story-format inspection, no restore."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import struct

from majiro_native_save import MAX_FILE_BYTES, MAX_RECORDS, SYSTEM_HEADER, _Pool

PROFILES = {b"MajiroSavV1.103\0": (0xA3E0, 0x90, 3),
            b"MajiroSavV1.105\0": (0xA560, 0x210, 4)}


def require(good, message):
    if not good:
        raise ValueError(message)


def parse_story_save(data):
    require(len(data) <= MAX_FILE_BYTES, "native story save exceeds size bound")
    require(data[:16] in PROFILES, "unsupported native story save signature")
    header_size, fields, pixel_bytes = PROFILES[data[:16]]
    require(len(data) >= header_size, "truncated native story header")
    depth, width, height, globals_, locals_, stack, pool_size = struct.unpack_from("<7I", data, fields)
    total = globals_ + locals_ + stack
    require(depth <= 1000, "story frame count exceeds bound")
    require(total <= MAX_RECORDS, "story combined value count exceeds bound")
    thumbnail_size = width * height * pixel_bytes
    require(thumbnail_size <= MAX_FILE_BYTES, "story thumbnail exceeds size bound")
    frame_size = (depth + 1) * 168
    value_size = total * 16
    require(header_size + thumbnail_size + frame_size + value_size + pool_size == len(data),
            "native story size mismatch or trailing data")
    require(SYSTEM_HEADER + value_size + pool_size <= MAX_FILE_BYTES,
            "story shared value view exceeds size bound")
    label = data[16:fields]
    require(b"\0" in label, "unterminated native story label")
    picture_offset = header_size - 40000
    cursor = header_size
    thumbnail = data[cursor:cursor+thumbnail_size]
    cursor += thumbnail_size
    frames, requirements = [], []
    for _ in range(depth + 1):
        raw = data[cursor:cursor+168]
        require(b"\0" in raw[:128] and raw[0] != 0, "unterminated or empty native story frame script")
        name = raw[:128].split(b"\0", 1)[0]
        values = list(struct.unpack_from("<10I", raw, 128))
        frames.append({"script_bytes_hex": name.hex(), "serialized_hex": raw.hex(), "fields": values})
        requirements.append({"script_bytes_hex": name.hex(), "line_id": values[4],
                             "code_offsets": [values[0]] + [v for v in values[5:] if v],
                             "identity_verified": False})
        cursor += 168
    pool_at = cursor + value_size
    pool = _Pool(data[pool_at:])
    groups = []
    for count in (globals_, locals_, stack):
        raw = data[cursor:cursor+count*16]
        values = []
        for hash_, tag, offset, bits in struct.iter_unpack("<4I", raw):
            require(tag <= 5, "unsupported native persistent-variable tag")
            if tag == 0:
                value = bits if bits < 0x80000000 else bits - 0x100000000
            elif tag == 1:
                value = {"float32_bits": f"{bits:08x}"}
            elif tag == 2:
                value = pool.string(offset)
            else:
                value = pool.array(offset, tag)
            values.append({"hash": f"{hash_:08x}", "native_type": tag, "value": value})
        groups.append({"serialized_slots_hex": raw.hex(),
                       "common_value_view": {"schema": 1, "format": "MajiroSysV1.100",
                                             "native_story_restore_supported": False,
                                             "variable_count": count, "object_pool_bytes": 0,
                                             "variables": values, "picture_basename_hashes": []}})
        cursor += count * 16
    return {"schema": 1, "format": data[:16].rstrip(b"\0").decode("ascii"),
            "native_story_restore_supported": False, "script_identity_verified": False,
            "resources_verified": False, "header_bytes_hex": data[:header_size].hex(),
            "label_bytes_hex": label.split(b"\0", 1)[0].hex(),
            "count_fields_offset": fields, "frame_depth": depth, "frame_stride": 168,
            "opaque_header": {"offset": fields+28, "bytes": picture_offset-fields-28,
                              "semantics_verified": False}, "picture_hash_offset": picture_offset,
            "thumbnail": {"width": width, "height": height, "pixel_bytes": pixel_bytes,
                          "bytes_hex": thumbnail.hex()}, "frames": frames,
            "groups": groups, "object_pool_hex": data[pool_at:].hex(),
            "script_requirements": requirements}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    require(args.source.stat().st_size <= MAX_FILE_BYTES, "native story save exceeds size bound")
    require(args.source.resolve() != args.output.resolve(), "refusing to overwrite source")
    result = parse_story_save(args.source.read_bytes())
    with args.output.open("x", encoding="utf8") as output:
        json.dump(result, output, ensure_ascii=True, indent=2)
        output.write("\n")


if __name__ == "__main__":
    main()
